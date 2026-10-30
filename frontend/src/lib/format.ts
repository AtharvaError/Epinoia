const number = new Intl.NumberFormat("en-IN");
const currency = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

export const formatNumber = (value: number | string) => number.format(Number(value));
export const formatMoney = (value: number | string) => currency.format(Number(value));

export function formatLakh(value: number) {
  return `₹${(value / 100000).toLocaleString("en-IN", { maximumFractionDigits: 1 })}L`;
}

export function formatTime(iso: string) {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
  }).format(new Date(iso));
}
