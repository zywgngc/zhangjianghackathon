import React from 'react';
import './styles.css';

export interface SettingsScreenProps {
  music: boolean;
  sfx: boolean;
  onToggleMusic: () => void;
  onToggleSfx: () => void;
  onBack: () => void;
}

export function SettingsScreen({
  music,
  sfx,
  onToggleMusic,
  onToggleSfx,
  onBack,
}: SettingsScreenProps): React.JSX.Element {
  return (
    <div className="ui-overlay settings-screen" role="dialog" aria-label="设置">
      <h2>设置</h2>
      <div className="settings-options">
        <button
          type="button"
          data-action="toggle-music"
          onClick={onToggleMusic}
          
          aria-pressed={music}
          className="settings-btn"
        >
          {music ? '音乐：开' : '音乐：关'}
        </button>
        <button
          type="button"
          data-action="toggle-sfx"
          onClick={onToggleSfx}
          
          aria-pressed={sfx}
          className="settings-btn"
        >
          {sfx ? '音效：开' : '音效：关'}
        </button>
      </div>
      <button
        type="button"
        data-action="menu"
        onClick={onBack}
        
        className="back-btn"
      >
        返回
      </button>
    </div>
  );
}
