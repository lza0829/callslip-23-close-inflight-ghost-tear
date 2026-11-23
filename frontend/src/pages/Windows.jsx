import { useEffect, useState } from 'react'
import { api } from '../api'

export default function Windows() {
  const [wins, setWins] = useState([])
  const [events, setEvents] = useState([])
  const [receipt, setReceipt] = useState(null)
  const [err, setErr] = useState('')
  const load = () =>
    Promise.all([api.windows(), api.events()])
      .then(([w, e]) => { setWins(w); setEvents(e) })
      .catch(e => setErr(e.message))
  useEffect(() => { load() }, [])
  const closeWin = async (w) => {
    setErr('')
    try { setReceipt(await api.closeWindow(w.id)); await load() } catch (e) { setErr(e.message) }
  }
  const openWin = async (w) => {
    setErr('')
    try { setReceipt(await api.openWindow(w.id)); await load() } catch (e) { setErr(e.message) }
  }
  // 拒收档后在办号继续可办结或过号，清掉指针即可再收档
  const resolve = async (fn, id) => {
    setErr('')
    try { await fn(id); setReceipt(null); await load() } catch (e) { setErr(e.message) }
  }
  return (
    <div className="page">
      <header className="hero">
        <p className="eyebrow">柜面收档</p>
        <h1>窗口</h1>
        <p className="lede">
          收档与在办联动：窗口仍钉着在办号时收档整笔失败（拒收档），须先办结或过号；
          收档成功即落事件，重复收档幂等重放同一回执。
        </p>
      </header>
      {err && <p className="err">{err}</p>}
      {receipt && (
        <section className="panel">
          <h2>
            收档回执 <span className="tag">{receipt.结果}</span>
            {receipt.重复 && <span className="tag">幂等重放</span>}
          </h2>
          <ul className="plain">
            <li>窗口：{receipt.窗口} · 当前{receipt.开放 ? '开放' : '已收档'}</li>
            <li>时刻：{receipt.时刻} · 事件编号：{receipt.事件编号 ?? '—'}</li>
            {receipt.拒因 && <li className="err">拒因：{receipt.拒因}</li>}
          </ul>
          {receipt.在办号牌 && (
            <div className="slip" style={{ marginTop: 12 }}>
              <div className="no">{receipt.在办号牌.号牌}</div>
              <div className="meta">{receipt.在办号牌.业务种类} · {receipt.在办号牌.状态}</div>
              <div className="actions">
                <button type="button" onClick={() => resolve(api.finish, receipt.在办号牌.id)}>办结</button>
                <button type="button" className="ghost" onClick={() => resolve(api.skip, receipt.在办号牌.id)}>过号</button>
              </div>
            </div>
          )}
        </section>
      )}
      <div className="card-grid">
        {wins.map(w => (
          <div className="panel" key={w.id}>
            <h2>{w.名称} <span className="tag">{w.开放 ? '开放' : '已收档'}</span></h2>
            <p className="muted">可办：{w.可办种类.map(k => k.名称).join('、') || '无'}</p>
            {w.当前号 ? (
              <div className="slip">
                <div className="no">{w.当前号.号牌}</div>
                <div className="meta">{w.当前号.状态}</div>
              </div>
            ) : <p className="muted">空闲</p>}
            <div className="actions">
              {w.开放 ? (
                <button type="button" className="ghost" onClick={() => closeWin(w)}>收档</button>
              ) : (
                <button type="button" onClick={() => openWin(w)}>开档</button>
              )}
            </div>
          </div>
        ))}
      </div>
      <section className="panel">
        <h2>窗口与叫号事件</h2>
        <ul className="plain">
          {events.map(e => (
            <li key={e.id}>{e.时刻} · {e.窗口} · {e.动作} · {e.号牌 || '—'}{e.备注 ? ` · ${e.备注}` : ''}</li>
          ))}
        </ul>
      </section>
    </div>
  )
}
