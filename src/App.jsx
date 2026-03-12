import { useState, useRef, useEffect } from 'react'
import { TrendingUp, AlertCircle, ChevronRight, X } from 'lucide-react'

export default function App() {
  // Estado de conexão
  const [isConnected, setIsConnected] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [apiToken, setApiToken] = useState('')
  const [loginError, setLoginError] = useState('')

  // Conta
  const [accountBalance, setAccountBalance] = useState('$0.00')
  const [accountType, setAccountType] = useState('---')

  // Gráfico e dados
  const [asset, setAsset] = useState('R_10')
  const [timeframe, setTimeframe] = useState('15M')
  const [currentPrice, setCurrentPrice] = useState('0.00')
  const [chartTitle, setChartTitle] = useState('Volatility 10')
  const [candleData, setCandleData] = useState([])

  // Trading
  const [stake, setStake] = useState('10')
  const [takeProfit, setTakeProfit] = useState('50')
  const [stopLoss, setStopLoss] = useState('50')
  const [signal, setSignal] = useState(null)
  const [aiMessage, setAiMessage] = useState('Conecte para começar...')
  const [isAnalyzing, setIsAnalyzing] = useState(false)

  // Contrato ativo
  const [activeContractId, setActiveContractId] = useState(null)
  const [contractPnL, setContractPnL] = useState(0)
  const [contractStatus, setContractStatus] = useState(null)
  const [isTradingStopped, setIsTradingStopped] = useState(false)

  // Refs
  const wsRef = useRef(null)
  const chartContainerRef = useRef(null)
  const chartRef = useRef(null)
  const candleSeriesRef = useRef(null)
  const smaSeriesRef = useRef(null)
  const APP_ID = 121512

  // Inicializar gráfico
  useEffect(() => {
    if (!isConnected || !chartContainerRef.current) return

    const initChart = async () => {
      try {
        let attempts = 0
        let LightweightCharts = window.LightweightCharts

        while (!LightweightCharts && attempts < 50) {
          await new Promise(r => setTimeout(r, 100))
          LightweightCharts = window.LightweightCharts
          attempts++
        }

        if (!LightweightCharts) {
          setAiMessage('❌ Erro ao carregar gráfico')
          return
        }

        if (chartRef.current) {
          chartRef.current.remove()
        }

        const rect = chartContainerRef.current.getBoundingClientRect()

        chartRef.current = LightweightCharts.createChart(chartContainerRef.current, {
          width: rect.width,
          height: rect.height,
          layout: {
            background: { type: 'solid', color: '#0f172a' },
            textColor: '#cbd5e1',
          },
          grid: {
            vertLines: { color: 'rgba(71, 85, 105, 0.3)' },
            horzLines: { color: 'rgba(71, 85, 105, 0.3)' },
          },
          timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#475569' },
          rightPriceScale: { borderColor: '#475569' },
        })

        candleSeriesRef.current = chartRef.current.addCandlestickSeries({
          upColor: '#22c55e',
          downColor: '#ef4444',
          borderVisible: false,
          wickUpColor: '#22c55e',
          wickDownColor: '#ef4444',
        })

        smaSeriesRef.current = chartRef.current.addLineSeries({
          color: '#3b82f6',
          lineWidth: 2,
        })

        const resizeObserver = new ResizeObserver(() => {
          if (chartRef.current && chartContainerRef.current) {
            const newRect = chartContainerRef.current.getBoundingClientRect()
            chartRef.current.applyOptions({
              width: newRect.width,
              height: newRect.height,
            })
          }
        })

        resizeObserver.observe(chartContainerRef.current)

        if (chartRef.current.timeScale) {
          chartRef.current.timeScale().fitContent()
        }

        setAiMessage('✅ Gráfico inicializado')
      } catch (error) {
        console.error('Erro ao inicializar gráfico:', error)
        setAiMessage('❌ Erro ao carregar gráfico')
      }
    }

    const timer = setTimeout(initChart, 100)
    return () => clearTimeout(timer)
  }, [isConnected])

  // Calcular SMA
  const calcSMA = (data, period) => {
    const sma = []
    for (let i = period - 1; i < data.length; i++) {
      const sum = data.slice(i - period + 1, i + 1).reduce((acc, c) => acc + c.close, 0)
      sma.push({
        time: data[i].time,
        value: sum / period,
      })
    }
    return sma
  }

  // Detectar SMC (BOS, OB)
  const detectSMC = (candles) => {
    if (candles.length < 50) return null

    const last50 = candles.slice(-50)
    const highs = last50.map(c => c.high)
    const lows = last50.map(c => c.low)

    const maxHigh = Math.max(...highs)
    const minLow = Math.min(...lows)
    const currentPrice = parseFloat(currentPrice || last50[last50.length - 1].close)

    // BOS (Break of Structure)
    const recentHigh = Math.max(...last50.slice(-10).map(c => c.high))
    const recentLow = Math.min(...last50.slice(-10).map(c => c.low))

    let signal = null

    // Sinal CALL (compra)
    if (currentPrice > recentHigh && currentPrice > minLow) {
      signal = {
        type: 'CALL',
        entry: currentPrice,
        tp: currentPrice + (parseFloat(takeProfit) / 100),
        sl: currentPrice - (parseFloat(stopLoss) / 100),
      }
    }

    // Sinal PUT (venda)
    if (currentPrice < recentLow && currentPrice < maxHigh) {
      signal = {
        type: 'PUT',
        entry: currentPrice,
        tp: currentPrice - (parseFloat(takeProfit) / 100),
        sl: currentPrice + (parseFloat(stopLoss) / 100),
      }
    }

    return signal
  }

  // Conectar WebSocket
  const handleConnect = async () => {
    if (!apiToken.trim()) {
      setLoginError('Insira um token válido')
      return
    }

    setIsLoading(true)
    setLoginError('')

    try {
      wsRef.current = new WebSocket(`wss://ws.derivws.com/websockets/v3?app_id=${APP_ID}`)

      wsRef.current.onopen = () => {
        wsRef.current?.send(JSON.stringify({ authorize: apiToken }))
      }

      wsRef.current.onmessage = (event) => {
        const data = JSON.parse(event.data)

        if (data.error) {
          setLoginError(data.error.message)
          setIsLoading(false)
          return
        }

        // Autorização
        if (data.msg_type === 'authorize') {
          setIsConnected(true)
          setIsLoading(false)
          setAccountBalance(`${data.authorize.balance} ${data.authorize.currency}`)
          setAccountType(data.authorize.is_virtual ? 'DEMO' : 'REAL')
          wsRef.current?.send(JSON.stringify({ balance: 1, subscribe: 1 }))
          loadAssetStream(asset)
        }

        // Atualizar saldo
        if (data.msg_type === 'balance') {
          setAccountBalance(`${data.balance.balance} ${data.balance.currency}`)
        }

        // Histórico de velas
        if (data.msg_type === 'candles') {
          const newCandleData = data.candles.map(c => ({
            time: c.epoch,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
          }))
          setCandleData(newCandleData)

          if (candleSeriesRef.current) {
            candleSeriesRef.current.setData(newCandleData)
          }

          if (smaSeriesRef.current) {
            smaSeriesRef.current.setData(calcSMA(newCandleData, 20))
          }
        }

        // Atualizar vela atual
        if (data.msg_type === 'ohlc') {
          const c = data.ohlc
          const liveCandle = {
            time: c.open_time,
            open: parseFloat(c.open),
            high: parseFloat(c.high),
            low: parseFloat(c.low),
            close: parseFloat(c.close),
          }

          if (candleData.length > 0 && candleSeriesRef.current) {
            try {
              candleSeriesRef.current.update(liveCandle)
            } catch (e) {
              console.warn('Erro ao atualizar vela:', e)
            }
          }

          const decimals = asset.includes('JPY') ? 3 : 5
          setCurrentPrice(liveCandle.close.toFixed(decimals))
        }

        // Compra bem-sucedida
        if (data.msg_type === 'buy') {
          const contractId = data.buy.contract_id
          setActiveContractId(contractId)
          setContractStatus('ABERTO')
          setAiMessage(`✅ Contrato aberto! ID: ${contractId}`)

          // Solicitar PnL ao vivo
          wsRef.current?.send(
            JSON.stringify({
              proposal_open_contract: 1,
              contract_id: contractId,
              subscribe: 1,
            })
          )
        }

        // PnL ao vivo
        if (data.msg_type === 'proposal_open_contract') {
          const contract = data.proposal_open_contract
          const pnl = contract.profit || 0

          setContractPnL(pnl)

          // Verificar TP/SL
          if (signal) {
            const tpValue = Math.abs(signal.tp - signal.entry) * parseFloat(stake)
            const slValue = Math.abs(signal.sl - signal.entry) * parseFloat(stake)

            if (pnl >= tpValue) {
              setAiMessage(`🎯 TAKE PROFIT ATINGIDO! Ganho: $${pnl.toFixed(2)}`)
              handleCloseContract(contractId)
            } else if (pnl <= -slValue) {
              setAiMessage(`⛔ STOP LOSS ATINGIDO! Perda: $${pnl.toFixed(2)}`)
              handleCloseContract(contractId)
            }
          }
        }

        // Contrato fechado
        if (data.msg_type === 'sell') {
          setContractStatus('FECHADO')
          setActiveContractId(null)
          setAiMessage(`✅ Contrato fechado com lucro/perda: $${contractPnL.toFixed(2)}`)
          wsRef.current?.send(JSON.stringify({ balance: 1 }))
        }
      }

      wsRef.current.onerror = () => {
        setLoginError('Erro ao conectar WebSocket')
        setIsLoading(false)
      }
    } catch (error) {
      setLoginError('Erro ao conectar')
      setIsLoading(false)
    }
  }

  // Carregar stream do ativo
  const loadAssetStream = (selectedAsset) => {
    if (!wsRef.current) return

    const timeframeMap = { '5M': 300, '15M': 900 }
    const granularity = timeframeMap[timeframe] || 900

    wsRef.current.send(
      JSON.stringify({
        ticks_history: selectedAsset,
        adjust_start_time: 1,
        count: 50,
        granularity,
        style: 'candles',
        subscribe: 1,
      })
    )

    setChartTitle(
      selectedAsset === 'R_10'
        ? 'Volatility 10'
        : selectedAsset === 'R_25'
          ? 'Volatility 25'
          : selectedAsset
    )
  }

  // Analisar gráfico
  const handleAnalyze = () => {
    if (candleData.length === 0) {
      setAiMessage('❌ Aguardando dados do gráfico...')
      return
    }

    setIsAnalyzing(true)

    setTimeout(() => {
      const detectedSignal = detectSMC(candleData)

      if (detectedSignal) {
        setSignal(detectedSignal)
        setAiMessage(
          `🎯 Sinal ${detectedSignal.type} detectado!\nEntry: ${detectedSignal.entry.toFixed(5)}\nTP: ${detectedSignal.tp.toFixed(5)}\nSL: ${detectedSignal.sl.toFixed(5)}`
        )
      } else {
        setAiMessage('⏳ Nenhum sinal detectado no momento')
      }

      setIsAnalyzing(false)
    }, 500)
  }

  // Executar trade
  const handleExecuteTrade = () => {
    if (!signal || !wsRef.current || isTradingStopped || activeContractId) return

    wsRef.current.send(
      JSON.stringify({
        buy: 1,
        price: parseFloat(stake),
        parameters: {
          amount: parseFloat(stake),
          basis: 'stake',
          contract_type: signal.type,
          currency: 'USD',
          duration: 1,
          duration_unit: 'm',
          symbol: asset,
        },
      })
    )
  }

  // Fechar contrato
  const handleCloseContract = (contractId) => {
    if (!wsRef.current) return

    wsRef.current.send(
      JSON.stringify({
        sell: contractId,
        price: 0,
      })
    )
  }

  // Parar trading
  const handleStopTrading = () => {
    setIsTradingStopped(!isTradingStopped)
    if (!isTradingStopped) {
      setAiMessage('⛔ TRADING PARADO')
    } else {
      setAiMessage('✅ TRADING RETOMADO')
    }
  }

  if (!isConnected) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center p-4">
        <div className="w-full max-w-md">
          <div className="bg-slate-800/50 border border-slate-700 rounded-2xl p-8 backdrop-blur-xl shadow-2xl">
            <div className="text-center mb-8">
              <div className="inline-flex items-center justify-center w-16 h-16 bg-blue-500/20 rounded-full mb-4">
                <TrendingUp className="w-8 h-8 text-blue-400" />
              </div>
              <h1 className="text-3xl font-bold text-white mb-2">
                SMC <span className="text-blue-400">TRADING BOT</span>
              </h1>
              <p className="text-xs text-slate-400 uppercase tracking-widest">
                Minimal • Static • GitHub Pages Ready
              </p>
            </div>

            <div className="space-y-5">
              <div>
                <label className="block text-xs text-slate-400 font-semibold mb-2 uppercase">
                  Token API Deriv
                </label>
                <input
                  type="password"
                  value={apiToken}
                  onChange={(e) => setApiToken(e.target.value)}
                  placeholder="Cole seu token..."
                  className="w-full bg-slate-900 border border-slate-600 rounded-lg px-4 py-3 text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
                />
              </div>

              {loginError && (
                <div className="bg-red-500/10 border border-red-500/30 rounded-lg p-3 flex items-start gap-2">
                  <AlertCircle className="w-4 h-4 text-red-400 mt-0.5 flex-shrink-0" />
                  <p className="text-sm text-red-300">{loginError}</p>
                </div>
              )}

              <button
                onClick={handleConnect}
                disabled={isLoading}
                className="w-full bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-semibold py-3 rounded-lg transition"
              >
                {isLoading ? (
                  <>
                    <span className="animate-spin mr-2">⟳</span>
                    CONECTANDO...
                  </>
                ) : (
                  <>
                    CONECTAR
                    <ChevronRight className="w-4 h-4 ml-2 inline" />
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-950 flex flex-col">
      {/* Header */}
      <header className="bg-slate-800/50 border-b border-slate-700 px-4 py-3 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto flex justify-between items-center">
          <div className="flex items-center gap-3">
            <TrendingUp className="w-5 h-5 text-blue-400" />
            <h1 className="font-bold text-white text-sm tracking-wide">SMC BOT MINIMAL</h1>
          </div>
          <div className="bg-slate-900/50 border border-slate-700 rounded-lg px-4 py-2">
            <div className="flex items-center gap-3">
              <span className="text-xs font-bold text-blue-400 bg-blue-500/20 px-2 py-1 rounded">
                {accountType}
              </span>
              <span className="text-slate-400 text-xs">Saldo:</span>
              <span className="text-green-400 font-mono font-bold">{accountBalance}</span>
            </div>
          </div>
        </div>
      </header>

      {/* Controles */}
      <div className="bg-slate-800/30 border-b border-slate-700 px-4 py-4 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto flex flex-wrap gap-3 items-end">
          <div className="flex flex-col">
            <label className="text-xs text-slate-400 font-semibold mb-2 uppercase">Ativo</label>
            <select
              value={asset}
              onChange={(e) => {
                setAsset(e.target.value)
                loadAssetStream(e.target.value)
              }}
              className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white"
            >
              <option value="R_10">Volatility 10</option>
              <option value="R_25">Volatility 25</option>
              <option value="frxEURUSD">EUR/USD</option>
              <option value="frxGBPUSD">GBP/USD</option>
            </select>
          </div>

          <div className="flex flex-col">
            <label className="text-xs text-slate-400 font-semibold mb-2 uppercase">Timeframe</label>
            <select
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
              className="bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white"
            >
              <option value="5M">5 Min</option>
              <option value="15M">15 Min</option>
            </select>
          </div>

          <div className="flex flex-col">
            <label className="text-xs text-slate-400 font-semibold mb-2 uppercase">Stake ($)</label>
            <input
              type="number"
              value={stake}
              onChange={(e) => setStake(e.target.value)}
              min="1"
              className="w-24 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white text-center"
            />
          </div>

          <div className="flex flex-col">
            <label className="text-xs text-slate-400 font-semibold mb-2 uppercase">TP ($)</label>
            <input
              type="number"
              value={takeProfit}
              onChange={(e) => setTakeProfit(e.target.value)}
              min="1"
              className="w-24 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white text-center"
            />
          </div>

          <div className="flex flex-col">
            <label className="text-xs text-slate-400 font-semibold mb-2 uppercase">SL ($)</label>
            <input
              type="number"
              value={stopLoss}
              onChange={(e) => setStopLoss(e.target.value)}
              min="1"
              className="w-24 bg-slate-900 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white text-center"
            />
          </div>

          <button
            onClick={handleAnalyze}
            disabled={isAnalyzing}
            className="bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-semibold px-6 py-2 rounded-lg transition"
          >
            {isAnalyzing ? '⟳ ANALISANDO...' : '📊 ANALISAR'}
          </button>

          <button
            onClick={handleStopTrading}
            className={`font-semibold px-6 py-2 rounded-lg transition ${
              isTradingStopped
                ? 'bg-red-600 hover:bg-red-700 text-white'
                : 'bg-green-600 hover:bg-green-700 text-white'
            }`}
          >
            {isTradingStopped ? '▶ RETOMAR' : '⛔ PARAR'}
          </button>
        </div>
      </div>

      {/* Conteúdo Principal */}
      <main className="flex-1 max-w-7xl mx-auto w-full p-4 grid grid-cols-1 lg:grid-cols-4 gap-4">
        {/* Gráfico */}
        <section className="lg:col-span-3 bg-slate-800/50 border border-slate-700 rounded-xl overflow-hidden relative shadow-xl">
          <div className="absolute top-4 left-4 z-10 bg-slate-900/80 border border-slate-700 px-3 py-2 rounded-lg flex gap-4 text-xs font-mono">
            <div>
              <span className="text-white font-bold">{chartTitle}</span>
            </div>
            <div>
              Preço: <span className="text-cyan-400 font-bold">{currentPrice}</span>
            </div>
          </div>
          <div
            ref={chartContainerRef}
            style={{
              position: 'relative',
              width: '100%',
              height: '500px',
              backgroundColor: '#0f172a',
            }}
          />
        </section>

        {/* Sidebar */}
        <section className="lg:col-span-1 flex flex-col gap-4">
          {/* IA */}
          <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-4 shadow-xl">
            <div className="flex items-center gap-2 mb-3 pb-3 border-b border-slate-700">
              <span>🧠</span>
              <h2 className="text-xs font-bold text-white uppercase tracking-wider">Analista</h2>
            </div>
            <div className="bg-slate-900/50 border border-slate-700 rounded-lg p-3 text-xs text-slate-300 font-mono min-h-[120px] overflow-y-auto whitespace-pre-wrap">
              {aiMessage}
            </div>
          </div>

          {/* Sinal */}
          {signal && (
            <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-5 shadow-xl flex-1 flex flex-col">
              <div className="text-center mb-4">
                <p className="text-xs text-slate-400 uppercase tracking-widest mb-1">Sinal SMC</p>
                <div
                  className={`text-4xl font-black tracking-wider ${
                    signal.type === 'CALL' ? 'text-green-400' : 'text-red-400'
                  }`}
                >
                  {signal.type === 'CALL' ? 'CALL ↗' : 'PUT ↘'}
                </div>
              </div>

              <div className="bg-slate-900/50 border border-slate-700 rounded-lg p-3 space-y-3 text-sm font-mono mb-6">
                <div className="flex justify-between pb-2 border-b border-slate-700">
                  <span className="text-slate-400">Entry:</span>
                  <span className="text-white font-bold">{signal.entry.toFixed(5)}</span>
                </div>
                <div className="flex justify-between pb-2 border-b border-slate-700">
                  <span className="text-green-400">TP:</span>
                  <span className="text-green-400 font-bold">{signal.tp.toFixed(5)}</span>
                </div>
                <div className="flex justify-between pb-2 border-b border-slate-700">
                  <span className="text-red-400">SL:</span>
                  <span className="text-red-400 font-bold">{signal.sl.toFixed(5)}</span>
                </div>
                <div className="flex justify-between pt-2 border-t border-slate-700">
                  <span className="text-slate-400">P&L:</span>
                  <span
                    className={`font-bold ${contractPnL >= 0 ? 'text-green-400' : 'text-red-400'}`}
                  >
                    ${contractPnL.toFixed(2)}
                  </span>
                </div>
              </div>

              {!activeContractId ? (
                <button
                  onClick={handleExecuteTrade}
                  disabled={isTradingStopped}
                  className="w-full bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white font-bold py-3 rounded-lg transition"
                >
                  ✅ EXECUTAR TRADE
                </button>
              ) : (
                <button
                  onClick={() => handleCloseContract(activeContractId)}
                  className="w-full bg-red-600 hover:bg-red-700 text-white font-bold py-3 rounded-lg transition"
                >
                  ❌ FECHAR CONTRATO
                </button>
              )}

              {contractStatus && (
                <div className="mt-3 text-center text-xs font-bold text-blue-400">
                  Status: {contractStatus}
                </div>
              )}
            </div>
          )}
        </section>
      </main>
    </div>
  )
}
