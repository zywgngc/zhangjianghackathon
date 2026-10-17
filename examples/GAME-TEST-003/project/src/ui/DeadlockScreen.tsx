import React from 'react';
import type { InputCommand } from '../game/types';

type Dispatch = (cmd: InputCommand) => void;

export interface DeadlockScreenProps {
  dispatch: Dispatch;
}

export const DeadlockScreen: React.FC<DeadlockScreenProps> = ({ dispatch }) => {
  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(80, 0, 0, 0.85)',
        color: '#fff',
        fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
        zIndex: 20,
      }}
    >
      <h1 style={{ fontSize: '2.5rem', marginBottom: '1rem' }}>死锁</h1>
      <p style={{ fontSize: '1.2rem', marginBottom: '2rem', textAlign: 'center', maxWidth: '80%' }}>
        当前局势已无法继续推进，请重新开始或返回主菜单。
      </p>
      <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', justifyContent: 'center' }}>
        <button
          type="button"
          data-action="restart"
          data-testid="deadlock-restart"
          onClick={() => dispatch({ type: 'restart' })}
          style={btnStyle}
        >
          重新开始
        </button>
        <button
          type="button"
          data-action="menu"
          data-testid="deadlock-menu"
          onClick={() => dispatch({ type: 'menu' })}
          style={btnStyle}
        >
          返回主菜单
        </button>
      </div>
    </div>
  );
};

const btnStyle: React.CSSProperties = {
  padding: '0.75rem 1.5rem',
  fontSize: '1.1rem',
  cursor: 'pointer',
  border: '2px solid #fff',
  borderRadius: '6px',
  background: 'rgba(255,255,255,0.15)',
  color: '#fff',
  minWidth: '140px',
};
