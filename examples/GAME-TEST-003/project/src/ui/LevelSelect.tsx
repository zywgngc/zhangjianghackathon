// src/ui/LevelSelect.tsx - Level selection screen for Power Hunter (百炼英雄)
// Owner: ui-team

import React from 'react';
import type { InputCommand, Snapshot } from '../game/types';

type Dispatch = (command: InputCommand) => void;

const LEVELS = [
  { id: 1, name: '森林营地' },
  { id: 2, name: '荒漠遗迹' },
  { id: 3, name: '魔王城堡' },
];

export const LevelSelect: React.FC<{ snapshot: Snapshot; dispatch: Dispatch }> = ({ snapshot, dispatch }) => {
  const unlockedLevel = snapshot.progress.unlockedLevel || 1;

  const handleSelect = (level: number) => {
    if (level <= unlockedLevel) {
      dispatch({ type: 'start', level });
    }
  };

  const handleBack = () => {
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
        color: '#fff',
        fontFamily: '"Microsoft YaHei", "PingFang SC", sans-serif',
        zIndex: 100,
      }}
    >
      <h1 style={{ fontSize: '2rem', marginBottom: '2rem', color: '#ffd700' }}>选择关卡</h1>
      <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', justifyContent: 'center' }}>
        {LEVELS.map((lvl) => {
          const isUnlocked = lvl.id <= unlockedLevel;
          return (
            <button
              key={lvl.id}
              type="button"
              data-action={`level-${lvl.id}`}
              data-testid={`level-${lvl.id}`}
              onClick={() => handleSelect(lvl.id)}
              disabled={!isUnlocked}
              style={{
                padding: '1.5rem 2rem',
                fontSize: '1.2rem',
                fontWeight: 'bold',
                border: '2px solid #ffd700',
                borderRadius: '8px',
                background: isUnlocked ? '#2a5298' : '#333',
                color: isUnlocked ? '#fff' : '#666',
                cursor: isUnlocked ? 'pointer' : 'not-allowed',
                minWidth: '160px',
                transition: 'transform 0.2s, background 0.2s',
              }}
              onMouseEnter={(e) => {
                if (isUnlocked) e.currentTarget.style.transform = 'scale(1.05)';
              }}
              onMouseLeave={(e) => {
                if (isUnlocked) e.currentTarget.style.transform = 'scale(1)';
              }}
            >
              <div>第 {lvl.id} 关</div>
              <div style={{ fontSize: '0.9rem', marginTop: '0.5rem' }}>{lvl.name}</div>
              {!isUnlocked && <div style={{ fontSize: '0.8rem', marginTop: '0.5rem', color: '#ff4444' }}>🔒 未解锁</div>}
            </button>
          );
        })}
      </div>
      <button
        type="button"
        data-action="menu"
        data-testid="back-to-menu"
        onClick={handleBack}
        style={{
          marginTop: '2rem',
          padding: '0.75rem 2rem',
          fontSize: '1rem',
          border: '1px solid #aaa',
          borderRadius: '4px',
          background: 'transparent',
          color: '#aaa',
          cursor: 'pointer',
        }}
      >
        返回主菜单
      </button>
    </div>
  );
};
