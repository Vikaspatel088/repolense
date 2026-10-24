/** Metric Card component */
import { renderButton } from './button';

export function renderCard(title: string, value: string): string {
  const btn = renderButton('Inspect', 'view-card');
  return `<div class="card"><h3>${title}</h3><p>${value}</p>${btn}</div>`;
}
