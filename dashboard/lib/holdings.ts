// Real Trading212 position snapshot: share counts and cost basis, entered
// manually (there's no public read API for Trading212). Lives in Supabase
// (holdings / portfolio_meta tables, see supabase/migrations/003_holdings.sql),
// not in this repo -- the repo is public, and these are real numbers.
//
// Update via Supabase directly (or a small admin form) after you
// buy/sell/rebalance -- there's no UI for this yet, it's a manual edit.

"use client";

import { useEffect, useState } from "react";
import { supabase } from "./supabase";

export type Holding = {
  symbol: string;
  name: string;
  shares: number;
  keywords: string[];
};

export type PortfolioMeta = {
  costBasisEur: number;
  asOf: string;
};

export function usePortfolio() {
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [meta, setMeta] = useState<PortfolioMeta | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      const [holdingsRes, metaRes] = await Promise.all([
        supabase.from("holdings").select("*").order("display_order"),
        supabase.from("portfolio_meta").select("*").eq("id", 1).maybeSingle(),
      ]);
      if (holdingsRes.data) {
        setHoldings(
          holdingsRes.data.map((h: any) => ({
            symbol: h.symbol,
            name: h.name,
            shares: h.shares,
            keywords: h.keywords ?? [],
          }))
        );
      }
      if (metaRes.data) {
        setMeta({ costBasisEur: metaRes.data.cost_basis_eur, asOf: metaRes.data.as_of });
      }
      setLoading(false);
    }
    load();
  }, []);

  return { holdings, meta, loading };
}
