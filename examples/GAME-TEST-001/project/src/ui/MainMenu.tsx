import React from 'react';
import './styles.css';

export interface MainMenuProps {
  onStart: () => void;
  onUpgrade: () => void;
  onSettings: () => void;
  gold: number;
  level?: number;
}

export function MainMenu({ onStart, onUpgrade, onSettings, gold, level=1 }: MainMenuProps): React.JSX.Element {
  return (
    <div className="ui-overlay" data-ui-root="MainMenu">
      <div className="ui-panel menu-panel">
        <h1 className="ui-title">数字门枪手</h1>
        <p>当前关卡：第 {level} 关</p>
        <p className="ui-gold" data-ui-value="score">金币：{gold}</p>
        <div className="ui-button-group">
          <button
            type="button"
            className="ui-btn ui-btn-primary"
            data-action="start"
            onClick={onStart}
            aria-label="开始游戏"
          >
            开始游戏
          </button>
          <button
            type="button"
            className="ui-btn"
            data-action="upgrades"
            onClick={onUpgrade}
            aria-label="升级"
          >
            升级
          </button>
          <button
            type="button"
            className="ui-btn"
            data-action="settings"
            onClick={onSettings}
            aria-label="设置"
          >
            设置
          </button>
        </div>
      </div>
    </div>
  );
}
