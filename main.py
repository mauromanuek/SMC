import asyncio
import json
import pandas as pd
import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ==========================================
# 1. CONFIGURAÇÕES GERAIS
# ==========================================
APP_ID = "121512"
DERIV_WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"

estado_bot = {
    "token_deriv": None,
    "modo_automatico": False,
    "ativo": "R_10",
    "stake": 10.0,
    "take_profit": 50.0,
    "stop_loss": 20.0,
    "lucro_diario": 0.0,
    "velas": [],
    "order_block": None
}

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])
conexoes_html = []

# ==========================================
# 2. COMUNICAÇÃO COM O CELULAR (HTML)
# ==========================================
async def enviar_log_html(mensagem, fonte="PYTHON", cor="text-gray-300"):
    pacote = json.dumps({"tipo": "log", "fonte": fonte, "mensagem": mensagem, "cor": cor})
    for conexao in conexoes_html:
        try: await conexao.send_text(pacote)
        except: pass

@app.websocket("/painel")
async def websocket_painel(websocket: WebSocket):
    await websocket.accept()
    conexoes_html.append(websocket)
    
    try:
        while True:
            dados = await websocket.receive_text()
            comando = json.loads(dados)
            
            if comando["tipo"] == "iniciar":
                estado_bot["token_deriv"] = comando["token"]
                estado_bot["ativo"] = comando["ativo"]
                await enviar_log_html(f"Motores ligados no ativo {estado_bot['ativo']}...", cor="text-blue-400")
                asyncio.create_task(motor_deriv_ws())
                
            elif comando["tipo"] == "toggle_auto":
                estado_bot["modo_automatico"] = comando["status"]
                
            elif comando["tipo"] == "configs":
                if estado_bot["ativo"] != comando["ativo"]:
                    estado_bot["ativo"] = comando["ativo"]
                    estado_bot["velas"] = [] # Limpa memória para o novo ativo
                    await enviar_log_html(f"Trocando radar para {estado_bot['ativo']}...", cor="text-yellow-400")
                
                estado_bot["stake"] = comando["stake"]
                estado_bot["take_profit"] = comando["tp"]
                estado_bot["stop_loss"] = comando["sl"]
                await enviar_log_html("Gerenciamento de Risco Atualizado e Travado.", cor="text-purple-400")

    except WebSocketDisconnect:
        conexoes_html.remove(websocket)

# ==========================================
# 3. MOTOR WEBSOCKET DA DERIV
# ==========================================
async def motor_deriv_ws():
    async with websockets.connect(DERIV_WS_URL) as ws_deriv:
        await ws_deriv.send(json.dumps({"authorize": estado_bot["token_deriv"]}))
        resp_auth = json.loads(await ws_deriv.recv())
        
        if "error" in resp_auth:
            await enviar_log_html(f"Erro Token: {resp_auth['error']['message']}", cor="text-red-500")
            return
            
        # Pede 1000 velas de 1 Minuto (60s) para ter mais agilidade na análise
        await ws_deriv.send(json.dumps({
            "ticks_history": estado_bot["ativo"], "adjust_start_time": 1,
            "count": 1000, "end": "latest", "style": "candles",
            "granularity": 60, "subscribe": 1
        }))

        while True:
            # Reconecta se trocar de ativo
            if not estado_bot["velas"]:
                await ws_deriv.send(json.dumps({
                    "ticks_history": estado_bot["ativo"], "adjust_start_time": 1,
                    "count": 1000, "end": "latest", "style": "candles",
                    "granularity": 60, "subscribe": 1
                }))
            
            mensagem = json.loads(await ws_deriv.recv())
            
            if "candles" in mensagem:
                estado_bot["velas"] = mensagem["candles"]
                await enviar_log_html("1.000 velas processadas. Calculando as 4 Camadas (SMC, RSI, MACD, Larry)...", cor="text-blue-400")
                
            elif "ohlc" in mensagem:
                vela = mensagem["ohlc"]
                if estado_bot["velas"] and estado_bot["velas"][-1]["epoch"] == int(vela["open_time"]):
                    estado_bot["velas"][-1] = { "epoch": int(vela["open_time"]), "open": float(vela["open"]), "high": float(vela["high"]), "low": float(vela["low"]), "close": float(vela["close"]) }
                else:
                    estado_bot["velas"].append({ "epoch": int(vela["open_time"]), "open": float(vela["open"]), "high": float(vela["high"]), "low": float(vela["low"]), "close": float(vela["close"]) })
                    if len(estado_bot["velas"]) > 1000: estado_bot["velas"].pop(0)

                await analisar_mercado_avancado(ws_deriv)

# ==========================================
# 4. CÉREBRO: AS 4 CAMADAS INSTITUCIONAIS
# ==========================================
async def analisar_mercado_avancado(ws_deriv):
    if len(estado_bot["velas"]) < 100: return
        
    df = pd.DataFrame(estado_bot["velas"])
    preco_atual = df.iloc[-1]['close']
    
    # ---------------------------------------------------------
    # MATEMÁTICA DOS INDICADORES (Camada 2 e 3)
    # ---------------------------------------------------------
    # 1. EMA 9 (Larry Williams)
    df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
    
    # 2. RSI 14 (Força Relativa)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    
    # 3. MACD (Convergência e Divergência)
    ema_12 = df['close'].ewm(span=12, adjust=False).mean()
    ema_26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema_12 - ema_26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()

    vela_atual = df.iloc[-1]
    vela_ant  = df.iloc[-2]
    vela_ant2 = df.iloc[-3]
    
    # ---------------------------------------------------------
    # CAMADA 1: CONTEXTO SMC (Mapeando OB)
    # ---------------------------------------------------------
    if estado_bot["order_block"] is None:
        # Caça a última vela vermelha antes de 3 velas verdes fortes (OB Alta)
        for i in range(len(df)-10, 50, -1):
            if df['close'].iloc[i] > df['open'].iloc[i] and df['close'].iloc[i-1] > df['open'].iloc[i-1] and df['close'].iloc[i-2] > df['open'].iloc[i-2]:
                if df['close'].iloc[i-3] < df['open'].iloc[i-3]:
                    estado_bot["order_block"] = {
                        "tipo": "BULLISH",
                        "maxima": df['high'].iloc[i-3],
                        "minima": df['low'].iloc[i-3]
                    }
                    await enviar_log_html(f"📍 SMC: Order Block detectado na zona {estado_bot['order_block']['minima']:.2f}. Aguardando o preço chegar lá.", cor="text-yellow-400")
                    break

    # ---------------------------------------------------------
    # A FUSÃO DAS CAMADAS (O TIRO)
    # ---------------------------------------------------------
    if estado_bot["order_block"]:
        ob = estado_bot["order_block"]
        
        # O preço entrou no Order Block? (Tira a trava de segurança)
        if ob["minima"] <= preco_atual <= (ob["maxima"] * 1.002):
            
            # CAMADA 2: O RSI está sobrevendido (< 40) e o MACD está cruzando pra cima?
            if vela_atual['RSI'] < 40 and vela_atual['MACD'] > vela_atual['Signal']:
                
                # CAMADA 3: Gatilho Larry Williams 9.1
                if vela_ant2['EMA_9'] > vela_ant['EMA_9'] and vela_atual['EMA_9'] > vela_ant['EMA_9']:
                    gatilho_compra = vela_ant['high']
                    
                    # O preço rompeu a máxima da vela de sinal?
                    if preco_atual > gatilho_compra:
                        await enviar_log_html(f"🔥 FILTROS ALINHADOS! (OB + RSI + MACD + LARRY 9.1).", cor="text-green-400 font-black")
                        await executar_ordem(ws_deriv, "CALL")
                        estado_bot["order_block"] = None # Reseta o bloco

# ==========================================
# 5. EXECUÇÃO E GERENCIAMENTO DE RISCO (Camada 4)
# ==========================================
async def executar_ordem(ws_deriv, direcao):
    # Verificação de Meta de Lucro/Perda
    if estado_bot["lucro_diario"] >= estado_bot["take_profit"]:
        await enviar_log_html(f"🏆 TAKE PROFIT DIÁRIO BATIDO (+${estado_bot['lucro_diario']})! Bot pausado.", cor="text-green-500 font-black")
        return
    if estado_bot["lucro_diario"] <= -estado_bot["stop_loss"]:
        await enviar_log_html(f"🩸 STOP LOSS ATINGIDO (-${abs(estado_bot['lucro_diario'])}). Bot pausado para proteger o capital.", cor="text-red-500 font-black")
        return

    if not estado_bot["modo_automatico"]:
        await enviar_log_html(f"Sinal Perfeito de {direcao} ignorado -> Modo Automático OFF.", cor="text-gray-500")
        await asyncio.sleep(10)
        return
        
    await enviar_log_html(f"💸 COMPRANDO CONTRATO: {direcao} a ${estado_bot['stake']}!", cor="text-green-500 font-bold")
    
    ordem = {
        "buy": 1,
        "price": estado_bot["stake"],
        "parameters": {
            "amount": estado_bot["stake"],
            "basis": "stake",
            "contract_type": direcao,
            "currency": "USD",
            "duration": 1, # Duração de 1 Minuto
            "duration_unit": "m",
            "symbol": estado_bot["ativo"]
        }
    }
    await ws_deriv.send(json.dumps(ordem))
    
    # Pausa o robô por 1 minuto enquanto a operação rola na corretora
    await asyncio.sleep(60)
    
    # (Opcional Futuro: Fazer um websocket paralelo só pra ler se o contrato deu Win ou Loss e atualizar a variável estado_bot["lucro_diario"]).

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
