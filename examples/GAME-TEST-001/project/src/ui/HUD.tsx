import React from 'react';

export interface HUDProps {
  soldiers: number;
  progress: number;
  bossHP: number;
  bossHealth?: number;
  bossMaxHealth?: number;
  level: number;
  onPause: () => void;
  onRestart?: () => void;
}

export function HUD({ soldiers, progress, bossHP, bossHealth=0, bossMaxHealth=0, level, onPause, onRestart }: HUDProps): React.ReactElement {
  const progressPercent = Math.min(100, Math.max(0, progress * 100));
  const bossVisible = bossHP > 0;

  return (
    <div className="hud-overlay" data-ui-root="HUD">
      <div className="hud-top-bar">
        <div className="hud-info">
          <span className="hud-level">关卡 {level}</span>
          <span className="hud-soldiers">士兵: {soldiers}</span>
        </div>
        <button
          type="button"
          className="hud-btn pause-btn"
          data-action="pause"
          onClick={onPause}
          aria-label="暂停游戏"
        >
          暂停
        </button>
        {onRestart&&<button type="button" data-action="restart" onClick={onRestart}>重开本关</button>}
      </div>

      <div className="hud-progress-container">
        <div className="hud-progress-label">进度</div>
        <div className="hud-progress-bar">
          <div
            className="hud-progress-fill"
            style={{ width: `${progressPercent}%` }}
            role="progressbar"
            aria-valuenow={Math.round(progressPercent)}
            aria-valuemin={0}
            aria-valuemax={100}
          />
        </div>
      </div>

      {bossVisible && (
        <div className="hud-boss-container">
          <div className="hud-boss-label">装甲车生命 {Math.ceil(bossHealth)}/{bossMaxHealth}</div>
          <div className="hud-boss-bar">
            <div
              className="hud-boss-fill"
              style={{ width: `${Math.min(100, Math.max(0, bossHP))}%` }}
              role="progressbar"
              aria-valuenow={Math.round(bossHP)}
              aria-valuemin={0}
              aria-valuemax={100}
            />
          </div>
        </div>
      )}

      <div className="hud-score" data-ui-value="score">
        士兵人数：{soldiers}
      </div>
    </div>
  );
}
