// PollingScheduler (FN-07) as a React hook. View states follow the state diagram:
// loading -> live; 2 failures or data age > 10 s -> stale; 5 failures -> error; tab hidden -> paused.
import { useCallback, useEffect, useRef, useState } from 'react'

const listeners = new Set()
let liveState = { state: 'loading', age: 0 }
export const liveBus = {
  set(s) { liveState = s; listeners.forEach((fn) => fn(s)) },
  subscribe(fn) { listeners.add(fn); fn(liveState); return () => listeners.delete(fn) },
}

export function usePoll(fetcher, deps = [], { interval = 2000, report = false } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [state, setState] = useState('loading')
  const [paused, setPaused] = useState(false)
  const fails = useRef(0)
  const lastOk = useRef(0)
  const timer = useRef(null)
  const alive = useRef(true)

  const tick = useCallback(async () => {
    clearTimeout(timer.current)
    if (document.hidden || paused) { setState('paused'); if (report) liveBus.set({ state: 'paused', age: 0 }); return }
    try {
      const d = await fetcher()
      if (!alive.current) return
      setData(d); setError(null); fails.current = 0; lastOk.current = Date.now()
    } catch (e) {
      if (!alive.current) return
      fails.current += 1; setError(e)
    }
    const age = lastOk.current ? (Date.now() - lastOk.current) / 1000 : 0
    const s = fails.current >= 5 ? 'error' : fails.current >= 2 || age > 10 ? 'stale' : 'live'
    setState(s)
    if (report) liveBus.set({ state: s, age: Math.round(age), at: lastOk.current })
    const delay = fails.current ? Math.min(2 * 2 ** fails.current, 30) * 1000 : interval
    timer.current = setTimeout(tick, delay)
  }, [...deps, paused]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    alive.current = true
    fails.current = 0
    tick()
    const onVis = () => tick()
    document.addEventListener('visibilitychange', onVis)
    return () => { alive.current = false; clearTimeout(timer.current); document.removeEventListener('visibilitychange', onVis) }
  }, [tick])

  return { data, error, state, paused, setPaused, refresh: tick }
}
