from datetime import date, timedelta


def _third_friday(year: int, month: int) -> date:
    first=date(year,month,1)
    return first+timedelta(days=((4-first.weekday())%7)+14)


def quarterly_contract(root: str, trading_day: date) -> dict:
    """TradingView symbol + Massive symbol, rolled by CME Monday convention.

    The contract is selected by the date the levels will be used, not by
    the prior cash-session date, so Monday roll calculations use the new
    contract's Friday bars for comparison.
    """
    if not isinstance(trading_day,date):
        raise ValueError("Giornata di analisi non valida")
    for yr in (trading_day.year,trading_day.year+1):
        for month,month_code in ((3,"H"),(6,"M"),(9,"U"),(12,"Z")):
            expiration=_third_friday(yr,month)
            roll_day=expiration-timedelta(days=4)
            if trading_day < roll_day:
                return {
                    "ticker":f"{root}{month_code}{yr%10}",
                    "tv_symbol":f"{root}{month_code}{yr}",
                    "roll_day":roll_day,
                    "expiry":expiration,
                }
    raise ValueError("Impossibile identificare il contratto trimestrale")
