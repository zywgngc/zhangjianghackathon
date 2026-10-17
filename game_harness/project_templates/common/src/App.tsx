import { useEffect, useRef, useState } from 'react';
import { mountGame, type GameHandle } from './engine';
export default function App() {
  const host = useRef<HTMLDivElement>(null);
  const game = useRef<GameHandle | null>(null);
  const [phase, setPhase] = useState('准备');
  useEffect(() => {
    if (!host.current) return;
    game.current = mountGame(host.current);
    return () => game.current?.destroy();
  }, []);
  return <main>
    <header><h1>游戏工程已初始化</h1><p>React 管理界面 · TypeScript 管理逻辑 · 本地引擎资源</p></header>
    <section className="toolbar"><span data-testid="game-status">{phase}</span><span data-testid="score">得分：0</span><span data-testid="time">时间：0</span>
      <button data-action="start" onClick={() => { game.current?.resume(); setPhase('运行'); }}>开始游戏</button>
      <button data-action="pause" onClick={() => { game.current?.pause(); setPhase('暂停'); }}>暂停</button>
      <button data-action="resume" onClick={() => { game.current?.resume(); setPhase('运行'); }}>继续</button>
      <button data-action="restart" onClick={() => { game.current?.restart(); setPhase('运行'); }}>重新开始</button>
    </section>
    <div ref={host} className="game-host" data-testid="game-root" />
    <p className="notice">此页面是引擎烟测模板；实际游戏由后续任务替换 src 模块。</p>
  </main>;
}
