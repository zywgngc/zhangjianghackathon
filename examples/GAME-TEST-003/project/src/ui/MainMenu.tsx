import React from 'react';
import type { InputCommand } from '../game/types';

export type Dispatch = (command: InputCommand) => void;

export interface MainMenuProps {
  dispatch: Dispatch;
}

export const MainMenu: React.FC<MainMenuProps> = ({ dispatch }) => {
  const handleStart = () => {
    dispatch({ type: 'start', level: 1 });
  };

  const handleLevelSelect = () => {
    dispatch({ type: 'openLevels' });
  };

  const handleSettings = () => dispatch({ type: 'settings' });

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
        background: 'rgba(0,0,0,0.85)',
        color: '#fff',
        fontFamily: 'sans-serif',
        zIndex: 100,
      }}
      data-testid="main-menu"
    >
      <h1 style={{ fontSize: '3rem', marginBottom: '2rem', textShadow: '0 0 10px #ffaa00' }}>
        百炼英雄
      </h1>
      <p style={{ fontSize: '1.2rem', marginBottom: '3rem', color: '#ccc' }}>战力吞噬者</p>

      <button
        type="button"
        data-action="start"
        onClick={handleStart}
        style={{
          padding: '1rem 3rem',
          fontSize: '1.5rem',
          margin: '0.5rem',
          cursor: 'pointer',
          background: '#d4a017',
          border: 'none',
          borderRadius: '8px',
          color: '#000',
          fontWeight: 'bold',
        }}
      >
        开始游戏
      </button>

      <button
        type="button"
        data-action="level-select"
        onClick={handleLevelSelect}
        style={{
          padding: '0.8rem 2.5rem',
          fontSize: '1.2rem',
          margin: '0.5rem',
          cursor: 'pointer',
          background: '#555',
          border: '1px solid #888',
          borderRadius: '8px',
          color: '#fff',
        }}
      >
        关卡选择
      </button>

      <button
        type="button"
        data-action="settings"
        onClick={handleSettings}
        style={{
          padding: '0.8rem 2.5rem',
          fontSize: '1.2rem',
          margin: '0.5rem',
          cursor: 'pointer',
          background: '#333',
          border: '1px solid #666',
          borderRadius: '8px',
          color: '#aaa',
        }}
      >
        设置
      </button>
    </div>
  );
};

export default MainMenu;
