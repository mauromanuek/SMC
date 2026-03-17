import asyncio
import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import websockets

app = FastAPI()
logging.basicConfig(level=logging.INFO)

APP_ID = 1089 
SYMBOL = "R_100" 

with open("index.html", "r", encoding="utf-8") as f:
    html_content = f.read()

@app.get("/")
async def get():
    return HTMLResponse(html_content)

class DerivBot:
    def __init__(self, client_ws: WebSocket):
        self.client_ws = client_ws
        self.deriv_ws = None
        self.token = None
        self.running = False
        self.auto_mode = False # NOVO: Controle de Modo Automático / Manual
        self.bot_status = "ANALYZING"
        
        self.ticks = [] # Armazena os dígitos (0-9)
        self.raw_prices = [] # NOVO: Armazena os preços reais para a estratégia Rise/Fall
        
        self.losses_in_row = 0
        self.balance = 0.0
        self.total_profit = 0.0
        
        self.trades_count = 0
        self.wins = 0
        self.losses = 0
        
        self.reanalyzing = False
        
        # Estratégia Ativa
        self.strategy = "MEGATRON" # Opções: MEGATRON, LOUCO, HALIKINA, FLASH
        
        # Gestão de Risco Padrão
        self.stake = 1.00
        self.recovery_stake = 2.50
        self.stop_loss = 10.00
        self.take_profit = 10.00
        
        # NOVO: Configurações exclusivas da Estratégia Louco (Rise/Fall)
        self.louco_duration_unit = "t" # "t" (ticks) ou "m" (minutos)
        self.louco_duration_value = 1  # 1 a 5

    async def connect_deriv(self, token):
        self.token = token
        uri = f"wss://ws.binaryws.com/websockets/v3?app_id={APP_ID}"
        try:
            self.deriv_ws = await websockets.connect(uri)
            await self.deriv_ws.send(json.dumps({"authorize": self.token}))
            auth_response = json.loads(await self.deriv_ws.recv())
            
            if "error" in auth_response:
                await self._send_to_frontend({"type": "auth_error", "msg": auth_response["error"]["message"]})
                return False

            self.balance = auth_response["authorize"]["balance"]
            await self._update_frontend_dashboard()
            await self._send_to_frontend({"type": "auth_success"})
            
            await self.deriv_ws.send(json.dumps({"balance": 1, "subscribe": 1}))
            await self.deriv_ws.send(json.dumps({"ticks_history": SYMBOL, "end": "latest", "count": 25, "style": "ticks"}))
            await self.deriv_ws.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))

            asyncio.create_task(self.listen_deriv())
            return True

        except Exception as e:
            logging.error(f"Erro conexão: {e}")
            await self._send_to_frontend({"type": "auth_error", "msg": "Falha na conexão com a Deriv."})
            return False

    async def listen_deriv(self):
        try:
            async for message in self.deriv_ws:
                data = json.loads(message)
                if "error" in data: continue

                if data.get("msg_type") == "history":
                    prices = data["history"]["prices"]
                    for price in prices:
                        self._process_tick(price)
                
                elif data.get("msg_type") == "tick":
                    price = data["tick"]["quote"]
                    self._process_tick(price)
                    await self.check_strategy()

                elif data.get("msg_type") == "balance":
                    self.balance = data["balance"]["balance"]
                    await self._update_frontend_dashboard()

                elif data.get("msg_type") == "proposal_open_contract":
                    contract = data["proposal_open_contract"]
                    if contract.get("is_sold"):
                        await self._handle_contract_closed(contract)

        except websockets.exceptions.ConnectionClosed:
            await asyncio.sleep(3)
            if self.token: await self.connect_deriv(self.token)

    def _process_tick(self, price):
        # Para a estratégia antiga (Dígitos)
        str_price = f"{float(price):.2f}"
        last_digit = str_price[-1] 
        self.ticks.append(last_digit)
        if len(self.ticks) > 25: self.ticks.pop(0)
            
        # Para a nova estratégia (Rise/Fall)
        self.raw_prices.append(float(price))
        if len(self.raw_prices) > 25: self.raw_prices.pop(0)
            
        asyncio.create_task(self._send_to_frontend({"type": "ticks_update", "ticks": self.ticks}))

    # MATEMÁTICA ESTRATÉGIA: MEGATRON (DÍGITOS)
    def _analyze_megatron(self):
        if len(self.ticks) < 25: return None
        percentages = {str(i): (self.ticks.count(str(i)) / 25) * 100 for i in range(10)}
        
        delay_9 = 0
        for tick in reversed(self.ticks):
            if tick == '9': break
            delay_9 += 1

        cluster_megatron = self.ticks[-10:].count('9') >= 3
        score = 0
        if percentages['9'] < 10: score += 1
        if 2 <= delay_9 <= 5: score += 1
        if '9' not in self.ticks[-3:]: score += 1
        if self.ticks[-12:].count('9') <= 1: score += 1

        return {"cluster": cluster_megatron, "score": score, "perc_9": percentages['9']}

    # MATEMÁTICA ESTRATÉGIA: LOUCO (RISE/FALL)
    def _analyze_louco(self):
        if len(self.raw_prices) < 10: return None
        
        # Calcula a direção dos movimentos (UP, DOWN, FLAT)
        movements = []
        for i in range(1, len(self.raw_prices)):
            if self.raw_prices[i] > self.raw_prices[i-1]: movements.append("UP")
            elif self.raw_prices[i] < self.raw_prices[i-1]: movements.append("DOWN")
            else: movements.append("FLAT")

        last_5 = movements[-5:]
        last_4 = movements[-4:]
        last_3 = movements[-3:]

        # Filtro de Segurança 1: 5 movimentos na mesma direção (Exaustão/Reversão)
        if len(set(last_5)) == 1 and last_5[0] != "FLAT": return None 

        # Filtro de Segurança 2: Mercado completamente lateralizado (Alternando)
        is_alternating = (last_4 == ["UP", "DOWN", "UP", "DOWN"] or last_4 == ["DOWN", "UP", "DOWN", "UP"])
        
        # Identifica Micro Tendência
        is_up_trend = all(m == "UP" for m in last_3)
        is_down_trend = all(m == "DOWN" for m in last_3)

        # Sistema de Score Louco
        score = 0
        if is_up_trend or is_down_trend: score += 2 # 3 mov iguais
        if not is_alternating: score += 1           # Sem alternância
        if "FLAT" not in last_5: score += 1         # Consistência limpa
        
        if score >= 3:
            return "CALL" if is_up_trend else "PUT"
        
        return None

    async def check_strategy(self):
        # SÓ OPERA SE O MODO AUTOMÁTICO ESTIVER LIGADO
        if not self.running or not self.auto_mode or self.bot_status != "ANALYZING" or self.reanalyzing:
            return

        # ==========================================
        # 1. ESTRATÉGIA: MEGATRON (DIFFERS 9)
        # ==========================================
        if self.strategy == "MEGATRON":
            analysis = self._analyze_megatron()
            if not analysis: return

            if analysis["cluster"]:
                self.bot_status = "PAUSED"
                asyncio.create_task(self._pause_and_reanalyze(10))
                return

            if self.losses_in_row == 0:
                if analysis["perc_9"] == 0 or analysis["score"] >= 3:
                    await self.execute_trade("DIGITDIFF", 9, self.stake, 1, "t")
            elif self.losses_in_row == 1:
                await self.execute_trade("DIGITOVER", 2, self.recovery_stake, 1, "t")

        # ==========================================
        # 2. ESTRATÉGIA: LOUCO (RISE/FALL)
        # ==========================================
        elif self.strategy == "LOUCO":
            if self.losses_in_row == 0:
                direction = self._analyze_louco()
                if direction: # CALL ou PUT
                    await self.execute_trade(direction, None, self.stake, self.louco_duration_value, self.louco_duration_unit)
            elif self.losses_in_row == 1:
                # Recuperação da Louco (Híbrida: usa dígitos para recuperar)
                await self.execute_trade("DIGITOVER", 2, self.recovery_stake, 1, "t")

        # As estratégias HALIKINA e FLASH apenas existem no menu por enquanto
        elif self.strategy in ["HALIKINA", "FLASH"]:
            pass

    async def execute_trade(self, contract_type, barrier, stake, duration, duration_unit):
        self.bot_status = "OPEN_CONTRACT"
        await self._update_frontend_dashboard()
        
        params = {
            "amount": stake,
            "basis": "stake",
            "contract_type": contract_type,
            "currency": "USD",
            "duration": duration,
            "duration_unit": duration_unit,
            "symbol": SYMBOL
        }
        
        if barrier is not None:
            params["barrier"] = str(barrier)
            
        req = { "buy": 1, "price": stake, "parameters": params }
        
        await self.deriv_ws.send(json.dumps(req))
        await self.deriv_ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1}))

    async def _handle_contract_closed(self, contract):
        profit = float(contract["profit"])
        is_win = profit > 0
        
        self.total_profit += profit
        self.trades_count += 1
        if is_win: self.wins += 1
        else: self.losses += 1

        # Pegar dígito final para regras de recuperação cruzada
        exit_tick = contract.get("exit_tick_display_value", "")
        exit_digit = exit_tick[-1] if exit_tick else "-"

        trade_data = {
            "type": contract["contract_type"],
            "tick": exit_digit,
            "stake": contract["buy_price"],
            "profit": profit
        }
        await self._send_to_frontend({"type": "trade_history", "data": trade_data})

        # GESTÃO DE RISCO E LÓGICA DE RECUPERAÇÃO
        if self.total_profit >= self.take_profit:
            self.bot_status = "STOPPED (META BATIDA)"
        elif self.total_profit <= -self.stop_loss:
            self.bot_status = "STOPPED (STOP LOSS)"
        else:
            if is_win:
                self.losses_in_row = 0 
                self.bot_status = "ANALYZING"
            else:
                # Se for a estratégia Louco e perdeu, SÓ entra em recuperação se o último dígito for 0 ou 9.
                if self.strategy == "LOUCO" and self.losses_in_row == 0:
                    if exit_digit in ['0', '9']:
                        self.losses_in_row = 1 # Aciona a recuperação DIGITOVER
                    else:
                        self.losses_in_row = 0 # Ignora, foi uma perda normal de Rise/Fall
                else:
                    self.losses_in_row += 1

                if self.losses_in_row >= 2:
                    logging.error("DUAS PERDAS! Pausando...")
                    self.losses_in_row = 0 
                    self.bot_status = "PAUSED"
                    asyncio.create_task(self._pause_and_reanalyze(15))
                else:
                    self.bot_status = "ANALYZING" 

        await self._send_to_frontend({"type": "status_update", "status": "CLOSED_CONTRACT"})
        await asyncio.sleep(1)
        await self._update_frontend_dashboard()

    async def _pause_and_reanalyze(self, seconds):
        self.reanalyzing = True
        await asyncio.sleep(seconds)
        self.reanalyzing = False
        if self.bot_status == "PAUSED":
            self.bot_status = "ANALYZING"
            await self._update_frontend_dashboard()

    async def _update_frontend_dashboard(self):
        await self._send_to_frontend({
            "type": "dashboard",
            "balance": self.balance,
            "profit": self.total_profit,
            "trades": self.trades_count,
            "wins": self.wins,
            "losses": self.losses,
            "status": self.bot_status,
            "auto_mode": self.auto_mode
        })

    async def _send_to_frontend(self, data):
        try:
            await self.client_ws.send_json(data)
        except: pass


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    bot = DerivBot(websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            command = data.get("action")
            
            if command == "connect":
                await bot.connect_deriv(data.get("token"))
            
            # Controle Principal (Start apenas liga o motor, AutoMode define se atira sozinho)
            elif command == "start":
                bot.running = True
                bot.bot_status = "ANALYZING"
                bot.losses_in_row = 0
                await bot._update_frontend_dashboard()
                
            elif command == "stop":
                bot.running = False
                bot.auto_mode = False
                bot.bot_status = "STOPPED"
                await bot._update_frontend_dashboard()
                
            # NOVO: Toggle Auto/Manual
            elif command == "toggle_auto":
                bot.auto_mode = data.get("auto")
                logging.info(f"MODO AUTOMÁTICO: {'LIGADO' if bot.auto_mode else 'DESLIGADO'}")
                await bot._update_frontend_dashboard()

            elif command == "reset_stats":
                bot.total_profit = 0.0
                bot.trades_count = 0
                bot.wins = 0
                bot.losses = 0
                bot.losses_in_row = 0
                await bot._update_frontend_dashboard()

            elif command == "set_strategy":
                bot.strategy = data.get("strategy")
                logging.info(f"Estratégia alterada para: {bot.strategy}")

            elif command == "update_settings":
                bot.stake = float(data.get("stake", bot.stake))
                bot.recovery_stake = float(data.get("recovery_stake", bot.recovery_stake))
                bot.stop_loss = float(data.get("stop_loss", bot.stop_loss))
                bot.take_profit = float(data.get("take_profit", bot.take_profit))
                
                # Novas configurações Rise/Fall
                bot.louco_duration_unit = data.get("louco_unit", "t")
                bot.louco_duration_value = int(data.get("louco_val", 1))

    except WebSocketDisconnect:
        bot.running = False
