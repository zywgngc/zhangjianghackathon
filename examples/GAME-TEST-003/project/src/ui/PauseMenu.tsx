import React from 'react';
import type { InputCommand } from '../game/types';

export type Dispatch = (cmd: InputCommand) => void;

export interface PauseMenuProps {
  dispatch: Dispatch;
}

export const PauseMenu: React.FC<PauseMenuProps> = ({ dispatch }) => {
  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: 'rgba(0, 0, 0, 0.7)',
        color: '#fff',
        fontFamily: 'sans-serif',
        zIndex: 100,
      }}
    >
      <h1 style={{ fontSize: '2rem', marginBottom: '1.5rem' }}>游戏暂停</h1>
      <button
        type="button"
        data-action="resume"
        data-testid="pause-resume"
        onClick={() => dispatch({ type: 'resume' })}
        style={{
          margin: '0.5rem',
          padding: '0.75rem 2rem',
          fontSize: '1.2rem',
          cursor: 'pointer',
          backgroundColor: '#4caf50',
          color: '#fff',
          border: 'none',
          borderRadius: '8px',
        }}
      >
        继续游戏
      </button>
      <button
        type="button"
        data-action="restart"
        data-testid="pause-restart"
        onClick={() => dispatch({ type: 'restart' })}
        style={{
          margin: '0.5rem',
          padding: '0.75rem 2rem',
          fontSize: '1.2rem',
          cursor: 'pointer',
          backgroundColor: '#ff9800',
          color: '#fff',
          border: 'none',
          borderRadius: '8px',
        }}
      >
        重新开始
      </button>
      <button
        type="button"
        data-action="menu"
        data-testid="pause-menu"
        onClick={() => dispatch({ type: 'menu' })}
        style={{
          margin: '0.5rem',
          padding: '0.75rem 2rem',
          fontSize: '1.2rem',
          cursor: 'pointer',
          backgroundColor: '#607d8b',
          color: '#fff',
          border: 'none',
          borderRadius: '8px',
        }}
      >
        返回主菜单
      </button>
    </div>
  );
};
