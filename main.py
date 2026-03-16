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
        self.bot_status = "ANALYZING"
        self.ticks = []
        self.losses_in_row = 0
        self.balance = 0.0
        self.total_profit = 0.0
        
        # Estatísticas
        self.trades_count = 0
        self.wins = 0
        self.losses = 0
        
        self.reanalyzing = False
        
        # Gestão de Risco Padrão
        self.stake = 1.00
        self.recovery_stake = 2.50
        self.stop_loss = 10.00
        self.take_profit = 10.00

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
        # CORREÇÃO: R_100 tem exatamente 2 casas decimais.
        # Formatamos para .2f para extrair o dígito real da Deriv.
        str_price = f"{float(price):.2f}"
        last_digit = str_price[-1]
        
        self.ticks.append(last_digit)
        if len(self.ticks) > 25:
            self.ticks.pop(0)
            
        asyncio.create_task(self._send_to_frontend({"type": "ticks_update", "ticks": self.ticks}))

    async def check_strategy(self):
        if not self.running or self.bot_status != "ANALYZING" or self.reanalyzing:
            return

        if len(self.ticks) < 25: return

        count_9 = self.ticks.count('9')
        perc_9 = (count_9 / 25) * 100

        if perc_9 > 16:
            self.bot_status = "PAUSED"
            await self._update_frontend_dashboard()
            asyncio.create_task(self._pause_and_reanalyze(10))
            return

        if perc_9 == 0 and self.losses_in_row == 0:
            await self.execute_trade("DIGITDIFF", 9, self.stake)

        elif self.losses_in_row == 1:
            await self.execute_trade("DIGITOVER", 2, self.recovery_stake)

    async def execute_trade(self, contract_type, prediction, stake):
        self.bot_status = "OPEN_CONTRACT"
        await self._update_frontend_dashboard()
        
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
        await self.deriv_ws.send(json.dumps({"proposal_open_contract": 1, "subscribe": 1}))

    async def _handle_contract_closed(self, contract):
        profit = float(contract["profit"])
        is_win = profit > 0

        self.total_profit += profit
        self.trades_count += 1
        
        # Contadores Separados
        if is_win: 
            self.wins += 1
        else:
            self.losses += 1

        trade_data = {
            "type": contract["contract_type"],
            "tick": contract.get("exit_tick_display_value", "")[-1] if contract.get("exit_tick_display_value") else "-",
            "stake": contract["buy_price"],
            "profit": profit
        }
        await self._send_to_frontend({"type": "trade_history", "data": trade_data})

        # Gestão de Risco
        if self.total_profit >= self.take_profit:
            self.running = False
            self.bot_status = "STOPPED (META BATIDA)"
        elif self.total_profit <= -self.stop_loss:
            self.running = False
            self.bot_status = "STOPPED (STOP LOSS)"
        else:
            if is_win:
                self.losses_in_row = 0
                self.bot_status = "ANALYZING"
            else:
                self.losses_in_row += 1
                if self.losses_in_row >= 2:
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
            "losses": self.losses,
            "status": self.bot_status
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
            
            elif command == "start":
                bot.running = True
                bot.bot_status = "ANALYZING"
                bot.losses_in_row = 0
                await bot._update_frontend_dashboard()
                
            elif command == "pause" or command == "stop":
                bot.running = False
                bot.bot_status = "STOPPED" if command == "stop" else "PAUSED"
                await bot._update_frontend_dashboard()

            elif command == "reset_stats":
                bot.total_profit = 0.0
                bot.trades_count = 0
                bot.wins = 0
                bot.losses = 0
                bot.losses_in_row = 0
                await bot._update_frontend_dashboard()

            elif command == "update_settings":
                bot.stake = float(data.get("stake", bot.stake))
                bot.recovery_stake = float(data.get("recovery_stake", bot.recovery_stake))
                bot.stop_loss = float(data.get("stop_loss", bot.stop_loss))
                bot.take_profit = float(data.get("take_profit", bot.take_profit))
                logging.info("Configurações atualizadas")

    except WebSocketDisconnect:
        bot.running = False
