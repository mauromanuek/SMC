import asyncio
import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import websockets

app = FastAPI()
logging.basicConfig(level=logging.INFO)

# Variáveis globais do Bot
APP_ID = 1089 # App ID genérico da Deriv
SYMBOL = "R_100" # Índice de Volatilidade 100
STAKE = 1.00
RECOVERY_STAKE = 2.50

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
        self.bot_status = "ANALYZING" # ANALYZING, OPEN_CONTRACT, PAUSED
        self.ticks = []
        self.stats = {str(i): 0 for i in range(10)}
        self.losses_in_row = 0
        self.balance = 0.0
        self.total_profit = 0.0
        self.trades_count = 0
        self.wins = 0
        self.current_contract_id = None
        self.reanalyzing = False

    async def connect_deriv(self, token):
        self.token = token
        uri = f"wss://ws.binaryws.com/websockets/v3?app_id={APP_ID}"
        try:
            self.deriv_ws = await websockets.connect(uri)
            # Autenticação
            await self.deriv_ws.send(json.dumps({"authorize": self.token}))
            auth_response = json.loads(await self.deriv_ws.recv())
            
            if "error" in auth_response:
                await self._send_to_frontend({"type": "auth_error", "msg": auth_response["error"]["message"]})
                return False

            self.balance = auth_response["authorize"]["balance"]
            await self._update_frontend_dashboard()
            await self._send_to_frontend({"type": "auth_success"})
            
            # Inscrever no saldo em tempo real
            await self.deriv_ws.send(json.dumps({"balance": 1, "subscribe": 1}))
            
            # Pegar últimos 25 ticks
            await self.deriv_ws.send(json.dumps({"ticks_history": SYMBOL, "end": "latest", "count": 25, "style": "ticks"}))
            
            # Inscrever em ticks ao vivo
            await self.deriv_ws.send(json.dumps({"ticks": SYMBOL, "subscribe": 1}))

            # Iniciar loop de escuta da Deriv
            asyncio.create_task(self.listen_deriv())
            return True

        except Exception as e:
            logging.error(f"Erro na conexão Deriv: {e}")
            await self._send_to_frontend({"type": "auth_error", "msg": "Falha na conexão com a Deriv."})
            return False

    async def listen_deriv(self):
        try:
            async for message in self.deriv_ws:
                data = json.loads(message)
                
                if "error" in data:
                    logging.error(f"Deriv Error: {data['error']['message']}")
                    continue

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
            logging.info("Deriv WS Closed. Tentando reconectar...")
            # O bot nunca desliga, tenta reconectar sozinho
            await asyncio.sleep(3)
            if self.token:
                await self.connect_deriv(self.token)

    def _process_tick(self, price):
        # Pega o último dígito
        str_price = f"{float(price):.5f}" # Garante casas decimais
        last_digit = str_price[-1]
        
        self.ticks.append(last_digit)
        if len(self.ticks) > 25:
            self.ticks.pop(0)
            
        self._calculate_stats()

    def _calculate_stats(self):
        if not self.ticks: return
        counts = {str(i): 0 for i in range(10)}
        for tick in self.ticks:
            counts[tick] += 1
            
        total = len(self.ticks)
        self.stats = {k: round((v / total) * 100) for k, v in counts.items()}
        asyncio.create_task(self._send_to_frontend({"type": "stats", "stats": self.stats}))

    async def check_strategy(self):
        if not self.running or self.bot_status != "ANALYZING" or self.reanalyzing:
            return

        if len(self.ticks) < 25:
            return

        # Verifica volatilidade absurda ou perigo repentino (Smart Pause)
        if self.stats["9"] > 16: # Se o 9 estiver saindo muito, pausa a análise normal
            self.bot_status = "PAUSED"
            await self._update_frontend_dashboard()
            asyncio.create_task(self._pause_and_reanalyze(10))
            return

        # ESTRATÉGIA PRINCIPAL: Differs 9 (Se 9 estiver 0%)
        if self.stats["9"] == 0 and self.losses_in_row == 0:
            await self.execute_trade("DIGITDIFF", 9, STAKE)

        # RECUPERAÇÃO: Over 2
        elif self.losses_in_row == 1:
            await self.execute_trade("DIGITOVER", 2, RECOVERY_STAKE)

    async def execute_trade(self, contract_type, prediction, stake):
        self.bot_status = "OPEN_CONTRACT"
        await self._update_frontend_dashboard()
        
        # Criação direta da proposta e compra (simplificada)
        req = {
            "buy": 1,
            "price": stake,
            "parameters": {
                "amount": stake,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": "USD",
                "duration": 1,
                "duration_unit": "t",
                "symbol": SYMBOL,
                "barrier": str(prediction)
            }
        }
        await self.deriv_ws.send(json.dumps(req))
        # Inscreve para monitorar contratos abertos
        await self.deriv_ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1}))

    async def _handle_contract_closed(self, contract):
        self.current_contract_id = None
        profit = float(contract["profit"])
        is_win = profit > 0

        self.total_profit += profit
        self.trades_count += 1
        if is_win: self.wins += 1

        trade_data = {
            "type": contract["contract_type"],
            "tick": contract.get("exit_tick_display_value", "")[-1] if contract.get("exit_tick_display_value") else "-",
            "stake": contract["buy_price"],
            "profit": profit
        }
        await self._send_to_frontend({"type": "trade_history", "data": trade_data})

        if is_win:
            self.losses_in_row = 0
            self.bot_status = "ANALYZING"
        else:
            self.losses_in_row += 1
            if self.losses_in_row >= 2:
                # Perdeu 2 vezes (Recuperação falhou) - PAUSA
                self.losses_in_row = 0
                self.bot_status = "PAUSED"
                asyncio.create_task(self._pause_and_reanalyze(15))
            else:
                self.bot_status = "ANALYZING"

        await self._update_frontend_dashboard()

    async def _pause_and_reanalyze(self, seconds):
        self.reanalyzing = True
        await asyncio.sleep(seconds)
        self.reanalyzing = False
        if self.running:
            self.bot_status = "ANALYZING"
            await self._update_frontend_dashboard()

    async def _update_frontend_dashboard(self):
        await self._send_to_frontend({
            "type": "dashboard",
            "balance": self.balance,
            "profit": self.total_profit,
            "trades": self.trades_count,
            "wins": self.wins,
            "status": self.bot_status,
            "recovery": self.losses_in_row > 0
        })

    async def _send_to_frontend(self, data):
        try:
            await self.client_ws.send_json(data)
        except:
            pass

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
            
            elif command == "start":
                bot.running = True
                bot.bot_status = "ANALYZING"
                await bot._update_frontend_dashboard()
                
            elif command == "pause":
                bot.running = False
                bot.bot_status = "PAUSED"
                await bot._update_frontend_dashboard()

            elif command == "stop":
                bot.running = False
                bot.bot_status = "STOPPED"
                bot.losses_in_row = 0
                await bot._update_frontend_dashboard()

    except WebSocketDisconnect:
        bot.running = False
        logging.info("Client disconnected")
