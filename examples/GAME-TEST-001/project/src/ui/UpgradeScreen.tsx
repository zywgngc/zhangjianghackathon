import React from 'react';
import './styles.css';

export interface UpgradeScreenProps {
  gold: number;
  soldierLv: number;
  damageLv: number;
  onBuy: (type: 'soldier' | 'damage') => void;
  onBack: () => void;
}

const MAX_LEVEL = 10;

function getCost(currentLevel: number): number {
  return 100 * (currentLevel + 1);
}

export function UpgradeScreen({ gold, soldierLv, damageLv, onBuy, onBack }: UpgradeScreenProps): React.JSX.Element {
  const soldierCost = getCost(soldierLv);
  const damageCost = getCost(damageLv);
  const canBuySoldier = soldierLv < MAX_LEVEL && gold >= soldierCost;
  const canBuyDamage = damageLv < MAX_LEVEL && gold >= damageCost;

  return (
    <div className="ui-overlay" role="dialog" aria-label="升级界面">
      <div className="ui-panel">
        <h2>升级</h2>
        <p data-ui-value="score">金币：{gold}</p>

        <p>初始士兵：{soldierLv+1} → {Math.min(11,soldierLv+2)}；武器伤害：{damageLv+1} → {Math.min(11,damageLv+2)}</p>
        <div className="upgrade-row">
          <span>初始士兵等级：{soldierLv}/{MAX_LEVEL}</span>
          <button
            type="button"
            data-action="purchase"
            disabled={!canBuySoldier}
            onClick={() => onBuy('soldier')}
            
            aria-label={`升级初始士兵，费用${soldierCost}金币`}
          >
            购买升级（{soldierCost}金币）
          </button>
        </div>

        <div className="upgrade-row">
          <span>武器伤害等级：{damageLv}/{MAX_LEVEL}</span>
          <button
            type="button"
            data-action="purchase"
            disabled={!canBuyDamage}
            onClick={() => onBuy('damage')}
            
            aria-label={`升级武器伤害，费用${damageCost}金币`}
          >
            购买升级（{damageCost}金币）
          </button>
        </div>

        <button
          type="button"
          data-action="menu"
          onClick={onBack}
          
          aria-label="返回主菜单"
        >
          返回
        </button>
      </div>
    </div>
  );
}
