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
        self.auto_mode = False 
        self.bot_status = "ANALYZING" # Pode ser ANALYZING, OPEN_CONTRACT, COOLDOWN, PAUSED, STOPPED
        
        self.ticks = [] 
        self.raw_prices = [] 
        
        self.losses_in_row = 0
        self.balance = 0.0
        self.total_profit = 0.0
        
        self.trades_count = 0
        self.wins = 0
        self.losses = 0
        
        self.reanalyzing = False
        self.strategy = "MEGATRON" 
        
        self.stake = 1.00
        self.recovery_stake = 2.50
        self.stop_loss = 10.00
        self.take_profit = 10.00
        
        self.louco_duration_unit = "t" 
        self.louco_duration_value = 1  
        
        self.halikina_type = "OVER"
        self.halikina_barrier = 4   
        self.halikina_duration_unit = "t" 
        self.halikina_duration_value = 1 

    async def connect_deriv(self, token):
        self.token = token
        uri = f"wss://ws.binaryws.com/websockets/v3?app_id={APP_ID}"
        try:
            self.deriv_ws = await websockets.connect(uri, ping_interval=20, ping_timeout=20) 
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
            await self._send_to_frontend({"type": "auth_error", "msg": "Falha conexão."})
            return False

    async def listen_deriv(self):
        try:
            async for message in self.deriv_ws:
                data = json.loads(message)
                if "error" in data: continue

                msg_type = data.get("msg_type")

                if msg_type == "tick":
                    price = data["tick"]["quote"]
                    self._process_tick(price)
                    await self.check_strategy()

                elif msg_type == "proposal_open_contract":
                    contract = data["proposal_open_contract"]
                    if contract.get("is_sold"):
                        await self._handle_contract_closed(contract)

                elif msg_type == "history":
                    for price in data["history"]["prices"]:
                        self._process_tick(price)

                elif msg_type == "balance":
                    self.balance = data["balance"]["balance"]
                    asyncio.create_task(self._update_frontend_dashboard()) 

        except websockets.exceptions.ConnectionClosed:
            await asyncio.sleep(2)
            if self.token: await self.connect_deriv(self.token)

    def _process_tick(self, price):
        str_price = f"{float(price):.2f}"
        self.ticks.append(str_price[-1])
        if len(self.ticks) > 25: self.ticks.pop(0)
            
        self.raw_prices.append(float(price))
        if len(self.raw_prices) > 25: self.raw_prices.pop(0)
            
        asyncio.create_task(self._send_to_frontend({
            "type": "ticks_update", 
            "ticks": self.ticks,
            "prices": self.raw_prices
        }))

    # 1. MEGATRON: Dinâmico para qualquer dígito zerado
    def _analyze_megatron(self):
        if len(self.ticks) < 25: return None
        counts = {str(i): self.ticks.count(str(i)) for i in range(10)}
        
        # Procura qualquer dígito que tenha frequência ZERO (0%)
        zero_digits = [int(d) for d, count in counts.items() if count == 0]
        
        return {"zero_digits": zero_digits}

    # 2. LOUCO: Rise / Fall
    def _analyze_louco(self):
        if len(self.raw_prices) < 10: return None
        movements = []
        for i in range(1, len(self.raw_prices)):
            if self.raw_prices[i] > self.raw_prices[i-1]: movements.append("UP")
            elif self.raw_prices[i] < self.raw_prices[i-1]: movements.append("DOWN")
            else: movements.append("FLAT")

        last_5 = movements[-5:]
        last_4 = movements[-4:]
        last_3 = movements[-3:]

        if len(set(last_5)) == 1 and last_5[0] != "FLAT": return None 
        is_alternating = (last_4 == ["UP", "DOWN", "UP", "DOWN"] or last_4 == ["DOWN", "UP", "DOWN", "UP"])
        is_up_trend = all(m == "UP" for m in last_3)
        is_down_trend = all(m == "DOWN" for m in last_3)

        score = 0
        if is_up_trend or is_down_trend: score += 2 
        if not is_alternating: score += 1           
        if "FLAT" not in last_5: score += 1         
        
        if score >= 3: return "CALL" if is_up_trend else "PUT"
        return None

    # 3. HALIKINA: Over 1 Fixo com Análise
    def _analyze_halikina(self):
        if len(self.ticks) < 25: return None
        target = self.halikina_barrier
        over_pct = (sum(1 for t in self.ticks if int(t) > target) / 25) * 100
        under_pct = (sum(1 for t in self.ticks if int(t) < target) / 25) * 100
        last_3 = [int(t) for t in self.ticks[-3:]]
        
        score = 0
        enter, contract = False, None

        if self.halikina_type == "OVER":
            if over_pct > 50: score += 2
            if all(t > target for t in last_3): score += 1
            if score >= 3: enter, contract = True, "DIGITOVER"
                
        elif self.halikina_type == "UNDER":
            if under_pct > 50: score += 2
            if all(t < target for t in last_3): score += 1
            if score >= 3: enter, contract = True, "DIGITUNDER"

        return {"enter": enter, "contract": contract, "barrier": target}

    # 4. FLASH: Analise Inteligente (Over 1 ou Under 8)
    def _analyze_flash(self):
        if len(self.ticks) < 25: return None
        
        # Riscos baseados na frequência de saída dos últimos 25 ticks
        risk_over_1 = self.ticks.count('0') + self.ticks.count('1')
        risk_under_8 = self.ticks.count('8') + self.ticks.count('9')
        
        # Momentum recente (Média dos últimos 3 ticks)
        avg_last_3 = sum(int(x) for x in self.ticks[-3:]) / 3

        contract, barrier = None, None

        # Exigimos que o risco de perda seja <= 1 ocorrência (<= 4%) para atirar
        if risk_over_1 <= 1 and risk_under_8 > 1:
            if avg_last_3 >= 3: # Tendência de alta confirmada
                contract, barrier = "DIGITOVER", 1
                
        elif risk_under_8 <= 1 and risk_over_1 > 1:
            if avg_last_3 <= 6: # Tendência de baixa confirmada
                contract, barrier = "DIGITUNDER", 8
                
        elif risk_over_1 <= 1 and risk_under_8 <= 1:
            # Se os dois estão excelentes, o Momentum desempata
            if avg_last_3 > 4.5: contract, barrier = "DIGITOVER", 1
            else: contract, barrier = "DIGITUNDER", 8

        return {"contract": contract, "barrier": barrier}

    async def check_strategy(self):
        if not self.running or not self.auto_mode or self.bot_status != "ANALYZING" or self.reanalyzing:
            return

        if self.strategy == "MEGATRON":
            if self.losses_in_row == 0:
                analysis = self._analyze_megatron()
                # Se a lista tiver algum dígito zerado, atira no primeiro que achar
                if analysis and len(analysis["zero_digits"]) > 0:
                    target_digit = analysis["zero_digits"][0]
                    await self.execute_trade("DIGITDIFF", target_digit, self.stake, 1, "t")
            elif self.losses_in_row == 1:
                await self.execute_trade("DIGITOVER", 2, self.recovery_stake, 1, "t")

        elif self.strategy == "LOUCO":
            if self.losses_in_row == 0:
                direction = self._analyze_louco()
                if direction: await self.execute_trade(direction, None, self.stake, self.louco_duration_value, self.louco_duration_unit)
            elif self.losses_in_row == 1:
                await self.execute_trade("DIGITOVER", 2, self.recovery_stake, 1, "t")

        elif self.strategy == "HALIKINA":
            if self.losses_in_row == 0:
                analysis = self._analyze_halikina()
                if analysis and analysis["enter"]:
                    await self.execute_trade(analysis["contract"], analysis["barrier"], self.stake, self.halikina_duration_value, self.halikina_duration_unit)
            elif self.losses_in_row == 1:
                contract = "DIGITOVER" if self.halikina_type == "OVER" else "DIGITUNDER"
                await self.execute_trade(contract, self.halikina_barrier, self.recovery_stake, self.halikina_duration_value, self.halikina_duration_unit)

        elif self.strategy == "FLASH":
            if self.losses_in_row == 0:
                analysis = self._analyze_flash()
                if analysis and analysis["contract"]:
                    await self.execute_trade(analysis["contract"], analysis["barrier"], self.stake, 1, "t")
            elif self.losses_in_row == 1:
                # Recuperação padrão do Flash tenta manter a segurança no meio
                await self.execute_trade("DIGITOVER", 2, self.recovery_stake, 1, "t")


    async def execute_trade(self, contract_type, barrier, stake, duration, duration_unit):
        self.bot_status = "OPEN_CONTRACT"
        params = {
            "amount": stake, "basis": "stake", "contract_type": contract_type,
            "currency": "USD", "duration": duration, "duration_unit": duration_unit, "symbol": SYMBOL
        }
        if barrier is not None: params["barrier"] = str(barrier)
        req = { "buy": 1, "price": stake, "parameters": params }
        
        await self.deriv_ws.send(json.dumps(req))
        await self.deriv_ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1}))
        asyncio.create_task(self._update_frontend_dashboard())

    async def _handle_contract_closed(self, contract):
        profit = float(contract["profit"])
        is_win = profit > 0
        
        self.total_profit += profit
        self.trades_count += 1
        if is_win: self.wins += 1
        else: self.losses += 1

        exit_tick = contract.get("exit_tick_display_value", "")
        exit_digit = exit_tick[-1] if exit_tick else "-"

        trade_data = {
            "type": contract["contract_type"],
            "tick": exit_digit,
            "stake": contract["buy_price"],
            "profit": profit
        }
        asyncio.create_task(self._send_to_frontend({"type": "trade_history", "data": trade_data}))
        asyncio.create_task(self._send_to_frontend({"type": "status_update", "status": "CLOSED_CONTRACT"}))

        if self.total_profit >= self.take_profit:
            self.bot_status = "STOPPED (META BATIDA)"
        elif self.total_profit <= -self.stop_loss:
            self.bot_status = "STOPPED (STOP LOSS)"
        else:
            if is_win:
                self.losses_in_row = 0 
                # COOLDOWN DE 2 SEGUNDOS ANTES DE VOLTAR A ANALISAR
                self.bot_status = "COOLDOWN"
                asyncio.create_task(self._cooldown_routine(2))
            else:
                self.losses_in_row += 1
                if self.losses_in_row >= 2:
                    self.losses_in_row = 0 
                    self.bot_status = "PAUSED"
                    asyncio.create_task(self._pause_and_reanalyze(10)) 
                else:
                    self.bot_status = "ANALYZING" 

        asyncio.create_task(self._update_frontend_dashboard())

    # Rotina para o delay de 2 segundos após vitória
    async def _cooldown_routine(self, seconds):
        await asyncio.sleep(seconds)
        if self.running and self.bot_status == "COOLDOWN":
            self.bot_status = "ANALYZING"
            asyncio.create_task(self._update_frontend_dashboard())

    async def _pause_and_reanalyze(self, seconds):
        self.reanalyzing = True
        await asyncio.sleep(seconds)
        self.reanalyzing = False
        if self.bot_status == "PAUSED":
            self.bot_status = "ANALYZING"
            asyncio.create_task(self._update_frontend_dashboard())

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
        try: await self.client_ws.send_json(data)
        except: pass


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    bot = DerivBot(websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            command = data.get("action")
            
            if command == "connect": await bot.connect_deriv(data.get("token"))
            elif command == "start":
                bot.running = True
                bot.bot_status = "ANALYZING"
                bot.losses_in_row = 0
                await bot._update_frontend_dashboard()
            elif command == "stop":
                bot.running = False
                bot.bot_status = "STOPPED"
                await bot._update_frontend_dashboard()
            elif command == "toggle_auto":
                bot.auto_mode = data.get("auto")
                await bot._update_frontend_dashboard()
                
            elif command == "manual_trade":
                if not bot.running or bot.bot_status not in ["ANALYZING", "COOLDOWN"]: continue
                strat = data.get("strat")
                
                if strat == "MEGATRON": 
                    # Tenta pegar um digito zerado. Se não achar, atira no 9 como segurança.
                    analysis = bot._analyze_megatron()
                    target = analysis["zero_digits"][0] if analysis and len(analysis["zero_digits"]) > 0 else 9
                    await bot.execute_trade("DIGITDIFF", target, bot.stake, 1, "t")
                    
                elif strat == "LOUCO": await bot.execute_trade(data.get("direction"), None, bot.stake, bot.louco_duration_value, bot.louco_duration_unit)
                elif strat == "HALIKINA": await bot.execute_trade(data.get("contract"), bot.halikina_barrier, bot.stake, bot.halikina_duration_value, bot.halikina_duration_unit)
                elif strat == "FLASH": await bot.execute_trade(data.get("contract"), data.get("barrier"), bot.stake, 1, "t")
                
            elif command == "reset_stats":
                bot.total_profit = 0.0; bot.trades_count = 0; bot.wins = 0; bot.losses = 0; bot.losses_in_row = 0
                await bot._update_frontend_dashboard()
                
            elif command == "set_strategy":
                bot.strategy = data.get("strategy")
                
            elif command == "update_settings":
                bot.stake = float(data.get("stake", bot.stake))
                bot.recovery_stake = float(data.get("recovery_stake", bot.recovery_stake))
                bot.stop_loss = float(data.get("stop_loss", bot.stop_loss))
                bot.take_profit = float(data.get("take_profit", bot.take_profit))
                bot.louco_duration_unit = data.get("louco_unit", "t")
                bot.louco_duration_value = int(data.get("louco_val", 1))
                bot.halikina_type = data.get("halikina_type", "OVER")
                bot.halikina_barrier = int(data.get("halikina_barrier", 4))
                bot.halikina_duration_unit = data.get("halikina_unit", "t")
                bot.halikina_duration_value = int(data.get("halikina_val", 1))

    except WebSocketDisconnect:
        bot.running = False
