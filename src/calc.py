
import pandas as pd

SUPERPAY_PCT = 0.014

def compute_row_auto_fields(row: pd.Series, superpay_pct: float):
    sys_cash = float(row.get("System amount Cash", 0) or 0)
    sys_card = float(row.get("System amount Card", 0) or 0)
    entered_cash = float(row.get("entered cash amount", 0) or 0)
    card_amt = float(row.get("Card amount", 0) or 0)

    expenses = sum(float(row.get(c, 0) or 0) for c in [
        "Employee advances", "Transportation Goods", "Transportation Allowance",
        "Cleaning", "Internet", "Cleaning supplies", "Bills", "Others"
    ])

    cashouts = float(row.get("Cashouts", 0) or 0)
    system_cashouts = float(row.get("system cashouts", 0) or 0)
    petty = float(row.get("Petty cash", 0) or 0)
    superpay_sent = float(row.get("SuperPay sent", 0) or 0)

    total_system_sales = sys_cash + sys_card
    total_sales = entered_cash + card_amt
    cash = entered_cash - cashouts - system_cashouts - expenses
    cash_deficit = sys_cash - entered_cash
    card_deficit = sys_card - card_amt

    superpay_expected = card_amt - (card_amt * superpay_pct)
    superpay_diff = superpay_expected - superpay_sent

    net_cash = cash - petty

    row["Total System Sales"] = total_system_sales
    row["Total Sales"] = total_sales
    row["Cash"] = cash
    row["Cash Deficit"] = cash_deficit
    row["Card Deficit"] = card_deficit
    row["SuperPay expected"] = round(superpay_expected, 2)
    row["SuperPay diff"] = round(superpay_diff, 2)
    row["net cash"] = net_cash

    return row

def recalc_forward(df: pd.DataFrame, start_idx: int = 0, superpay_pct: float = SUPERPAY_PCT):
    df = df.copy().reset_index(drop=True)
    for i in range(start_idx, len(df)):
        df.loc[i] = compute_row_auto_fields(df.loc[i], superpay_pct)
        if i == 0:
            df.loc[i, "Accumulative cash"] = df.loc[i].get("net cash", 0)
            df.loc[i, "Accumulative card"] = df.loc[i].get("Card amount", 0)
        else:
            df.loc[i, "Accumulative cash"] = float(df.loc[i-1].get("Accumulative cash", 0) or 0) + float(df.loc[i].get("net cash", 0) or 0)
            df.loc[i, "Accumulative card"] = float(df.loc[i-1].get("Accumulative card", 0) or 0) + float(df.loc[i].get("Card amount", 0) or 0)
        df.loc[i, "Total Money"] = float(df.loc[i].get("Accumulative cash", 0) or 0) + float(df.loc[i].get("Accumulative card", 0) or 0)
    return df
