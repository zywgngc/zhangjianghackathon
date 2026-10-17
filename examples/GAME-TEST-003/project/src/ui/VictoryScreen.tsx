import React from 'react';
import type { InputCommand } from '../game/types';

type Dispatch = (command: InputCommand) => void;

export interface VictoryScreenProps {
  snapshot: {
    level: number;
    progress: {
      unlockedLevel: number;
      completedLevels: number[];
    };
  };
  dispatch: Dispatch;
}

export const VictoryScreen: React.FC<VictoryScreenProps> = ({ snapshot, dispatch }) => {
  const hasNextLevel = snapshot.level < 3 && snapshot.progress.unlockedLevel > snapshot.level;

  const handleNextLevel = () => {
    const nextLevel = Math.min(snapshot.level + 1, 3);
    dispatch({ type: 'start', level: nextLevel });
  };

  const handleRestart = () => {
    dispatch({ type: 'restart' });
  };

  const handleMenu = () => {
    dispatch({ type: 'menu' });
  };

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
        background: 'rgba(0, 0, 0, 0.85)',
        color: '#ffd700',
        fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
        zIndex: 100,
      }}
      data-testid="victory-screen"
    >
      <h1 style={{ fontSize: '2.5rem', marginBottom: '1rem', textShadow: '0 0 10px #ffd700' }}>
        闯关成功
      </h1>
      <p style={{ fontSize: '1.2rem', marginBottom: '0.5rem', color: '#fff' }}>
        恭喜通关第 {snapshot.level} 关！
      </p>
      <p style={{ fontSize: '1rem', marginBottom: '2rem', color: '#ccc' }}>
        你已击败本关首领，新的冒险已经解锁。
      </p>

      <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', justifyContent: 'center' }}>
        {hasNextLevel && (
          <button
            type="button"
            data-action="next-level"
            data-testid="btn-next-level"
            onClick={handleNextLevel}
            style={{
              padding: '0.75rem 2rem',
              fontSize: '1.1rem',
              background: '#4caf50',
              color: '#fff',
              border: 'none',
              borderRadius: '8px',
              cursor: 'pointer',
              fontWeight: 'bold',
            }}
          >
            下一关
          </button>
        )}

        <button
          type="button"
          data-action="restart"
          data-testid="btn-restart"
          onClick={handleRestart}
          style={{
            padding: '0.75rem 2rem',
            fontSize: '1.1rem',
            background: '#2196f3',
            color: '#fff',
            border: 'none',
            borderRadius: '8px',
            cursor: 'pointer',
            fontWeight: 'bold',
          }}
        >
          重玩本关
        </button>

        <button
          type="button"
          data-action="menu"
          data-testid="btn-menu"
          onClick={handleMenu}
          style={{
            padding: '0.75rem 2rem',
            fontSize: '1.1rem',
            background: '#666',
            color: '#fff',
            border: 'none',
            borderRadius: '8px',
            cursor: 'pointer',
            fontWeight: 'bold',
          }}
        >
          返回主菜单
        </button>
      </div>
    </div>
  );
};

export default VictoryScreen;
