import React from 'react';
import './styles.css';

export interface DefeatScreenProps {
  reason: string;
  onRetry: () => void;
  onMenu: () => void;
}

export function DefeatScreen({ reason, onRetry, onMenu }: DefeatScreenProps): React.ReactElement {
  return (
    <div className="ui-overlay" role="dialog" aria-label="失败界面">
      <div className="ui-panel defeat-panel">
        <h2 className="ui-title">挑战失败</h2>
        <p className="defeat-reason">{reason}</p>
        <div className="ui-buttons">
          <button
            type="button"
            className="ui-btn primary-btn"
            data-action="restart"
            onClick={onRetry}
            aria-label="重新开始"
          >
            重新开始
          </button>
          <button
            type="button"
            className="ui-btn secondary-btn"
            data-action="menu"
            onClick={onMenu}
            aria-label="返回主菜单"
          >
            返回主菜单
          </button>
        </div>
      </div>
    </div>
  );
}
