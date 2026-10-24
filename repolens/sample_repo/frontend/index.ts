/** Client Entry Point */
import { renderCard } from './components/card';
import { formatCurrency } from './utils/format';

export interface DashboardState {
  loaded: boolean;
  activeId: number;
}

export function initDashboard(state: DashboardState): void {
  const amount = formatCurrency(2500);
  const cardHtml = renderCard('Telemetry View', amount);
  console.log('Dashboard active:', cardHtml);
}
