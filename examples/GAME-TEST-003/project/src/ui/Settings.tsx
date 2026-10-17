import React from 'react';
import type { InputCommand } from '../game/types';

type Dispatch = (command: InputCommand) => void;

export interface SettingsProps {
  music?: boolean;
  sfx?: boolean;
  dispatch: Dispatch;
}

const overlayStyle: React.CSSProperties = {
  position: 'absolute',
  inset: 0,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  background: 'rgba(0,0,0,0.85)',
  color: '#fff',
  zIndex: 1000,
  fontFamily: 'sans-serif',
};

const panelStyle: React.CSSProperties = {
  background: 'rgba(30,30,60,0.95)',
  padding: '2rem',
  borderRadius: '12px',
  minWidth: '280px',
  textAlign: 'center',
};

const btnStyle: React.CSSProperties = {
  margin: '0.5rem',
  padding: '0.6rem 1.5rem',
  fontSize: '1rem',
  cursor: 'pointer',
  border: 'none',
  borderRadius: '6px',
  background: '#4a6fa5',
  color: '#fff',
};

const toggleBtnStyle = (on: boolean): React.CSSProperties => ({
  ...btnStyle,
  background: on ? '#2e7d32' : '#c62828',
});

export const Settings: React.FC<SettingsProps> = ({ music = true, sfx = true, dispatch }) => {
  const handleToggleMusic = () => {
    dispatch({ type: 'setAudio', music: !music });
  };

  const handleToggleSfx = () => {
    dispatch({ type: 'setAudio', sfx: !sfx });
  };

  const handleBack = () => {
    dispatch({ type: 'menu' });
  };

  return (
    <div style={overlayStyle} data-testid="settings-screen">
      <div style={panelStyle}>
        <h2 style={{ marginTop: 0 }}>设置</h2>

        <div style={{ margin: '1rem 0' }}>
          <button
            type="button"
            style={toggleBtnStyle(music)}
            data-action="toggle-music"
            data-testid="toggle-music"
            onClick={handleToggleMusic}
          >
            音乐：{music ? '开' : '关'}
          </button>
        </div>

        <div style={{ margin: '1rem 0' }}>
          <button
            type="button"
            style={toggleBtnStyle(sfx)}
            data-action="toggle-sfx"
            data-testid="toggle-sfx"
            onClick={handleToggleSfx}
          >
            音效：{sfx ? '开' : '关'}
          </button>
        </div>

        <div style={{ marginTop: '1.5rem' }}>
          <button
            type="button"
            style={btnStyle}
            data-action="menu"
            data-testid="settings-back"
            onClick={handleBack}
          >
            返回
          </button>
        </div>
      </div>
    </div>
  );
};

export default Settings;
