/** Value formatting utilities */
export function formatCurrency(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

export function formatDate(isoStr: string): string {
  return new Date(isoStr).toLocaleDateString();
}
