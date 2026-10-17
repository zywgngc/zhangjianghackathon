import React from 'react';

export interface PauseMenuProps {
  onResume: () => void;
  onRestart: () => void;
  onMenu: () => void;
}

export function PauseMenu({ onResume, onRestart, onMenu }: PauseMenuProps): React.ReactElement {
  return (
    <div className="ui-overlay pause-menu" role="dialog" aria-label="暂停菜单">
      <h2>游戏暂停</h2>
      <div className="button-group">
        <button
          type="button"
          data-action="resume"
          onClick={onResume}
          
          aria-label="继续游戏"
        >
          继续
        </button>
        <button
          type="button"
          data-action="restart"
          onClick={onRestart}
          
          aria-label="重新开始"
        >
          重新开始
        </button>
        <button
          type="button"
          data-action="menu"
          onClick={onMenu}
          
          aria-label="返回主菜单"
        >
          主菜单
        </button>
      </div>
    </div>
  );
}
