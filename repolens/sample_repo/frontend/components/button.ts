/** Action Button component */
export function renderButton(label: string, actionId: string): string {
  return `<button data-action="${actionId}">${label}</button>`;
}
