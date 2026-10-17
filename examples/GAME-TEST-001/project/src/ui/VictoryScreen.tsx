import React from 'react';

export interface VictoryScreenProps {
  gold: number;
  level: number;
  onNext: () => void;
  onMenu: () => void;
  onReplay: () => void;
}

export function VictoryScreen({ gold, level, onNext, onMenu, onReplay }: VictoryScreenProps): React.JSX.Element {
  return (
    <div className="ui-overlay victory-screen" role="dialog" aria-label="胜利">
      <h1>闯关成功</h1>
      <p>第 {level} 关完成</p>
      <p data-ui-value="score">获得金币：{gold}</p>
      <div className="button-group">
        <button
          type="button"
          data-action={level < 3 ? `level-${level + 1}` : 'menu'}
          onClick={onNext}
          
          aria-label="下一关"
        >
          {level < 3 ? '下一关' : '全部通关，返回主页'}
        </button>
        <button
          type="button"
          data-action="restart"
          onClick={onReplay}
          
          aria-label="重玩本关"
        >
          重玩本关
        </button>
        <button
          type="button"
          data-action="menu"
          onClick={onMenu}
          
          aria-label="返回主菜单"
        >
          返回主菜单
        </button>
      </div>
    </div>
  );
}
