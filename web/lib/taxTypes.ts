// Types for the tax-aware de-risking feature (/tax page).
// Mirrors service/routers/tax.py response shapes.

export interface TaxLot {
  shares: number;
  cost_basis_per_share: number;
  acquired_date: string;
  term: "long" | "short";
  plan_type: string;
  account: string;
  market_value: number;
  embedded_gain: number;
}

export interface TaxPosition {
  symbol: string;
  price: number;
  shares: number;
  value: number;
  cost: number;
  embedded_gain: number;
  long_term_gain: number;
  short_term_gain: number;
  long_term_loss: number;
  short_term_loss: number;
  lot_count: number;
  lots: TaxLot[];
}

export interface RatePreset {
  long_term: number;
  short_term: number;
  ordinary: number;
}

export interface TaxLotsResponse {
  positions: TaxPosition[];
  total_value: number;
  embedded: {
    long_term_gain: number;
    short_term_gain: number;
    long_term_loss: number;
    short_term_loss: number;
  };
  concentration: { symbol: string | null; pct: number | null };
  rate_presets: Record<string, RatePreset>;
}

export interface DeriskMethodResult {
  method: string;
  target_value: number;
  proceeds: number;
  shares_sold: number;
  realized_gain: number;
  long_term_gain: number;
  short_term_gain: number;
  tax: number;
  net_cash: number;
  tax_drag_pct: number;
  slices: Array<{
    symbol: string;
    shares: number;
    cost_basis_per_share: number;
    acquired_date: string;
    term: string;
    plan_type: string;
    proceeds: number;
    gain: number;
  }>;
}

export interface DeriskResponse {
  symbol: string;
  price: number;
  position: TaxPosition;
  rates: RatePreset;
  comparison: DeriskMethodResult[];
  best: DeriskMethodResult | null;
}

export interface HarvestResponse {
  harvestable_lots: Array<{
    symbol: string;
    shares: number;
    acquired_date: string;
    term: string;
    loss: number;
  }>;
  long_term_loss: number;
  short_term_loss: number;
  total_loss: number;
  tax_offset_value: number;
}

export interface CharitableResponse {
  symbol: string;
  price: number;
  donate: {
    donated_value: number;
    shares_donated: number;
    embedded_gain_avoided: number;
    cap_gains_tax_avoided: number;
    income_deduction_value: number;
    total_tax_benefit: number;
  };
  sell_equivalent: DeriskMethodResult;
  advantage_vs_selling: number;
}

export interface DeriskRequest {
  symbol: string;
  target_value: number;
  methods?: string[];
  rate_preset?: string;
}

export interface CharitableRequest {
  symbol: string;
  donate_value: number;
  rate_preset?: string;
}
