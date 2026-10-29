"""Verifiche synthetic: Asia 5m incompleta, 15m/30m completi.
Test offline: non chiama Yahoo e non altera le formule NET GAMMA.
"""
import unittest
from datetime import date, datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo
import pandas as pd

from asia_gamma import _complete_higher_timeframe, asia_range_reading

ROME=ZoneInfo("Europe/Rome")
DAY=date(2026,10,29)
START=datetime.combine(DAY,time(0,0),ROME)


def candles(minutes, missing_at=None):
    times=pd.date_range(START,periods=540//minutes,freq=f"{minutes}min")
    data=pd.DataFrame(
        {"Open":100.0,"High":102.0,"Low":98.0,"Close":100.0},
        index=times)
    if minutes == 15:
        data.loc[START.replace(hour=5,minute=0),"High"]=110.0
        data.loc[START.replace(hour=5,minute=0),"Low"]=90.0
    if minutes == 30:
        data.loc[START.replace(hour=5,minute=0),"High"]=109.0
        data.loc[START.replace(hour=5,minute=0),"Low"]=91.0
    if missing_at is not None:
        data=data.drop(START.replace(hour=missing_at[0],minute=missing_at[1]))
    return data


class TestAsiaHigherTimeframe(unittest.TestCase):
    def setUp(self):
        self.five=candles(5,missing_at=(5,0))
        self.assertEqual(len(self.five),107)

    def test_15m_complete_certifies_high_low_and_nine_close(self):
        with patch("yfinance.download",return_value=candles(15)) as get:
            result=_complete_higher_timeframe(DAY,self.five)
        self.assertEqual(get.call_count,1)
        self.assertEqual(result["resolution"],"15m")
        self.assertEqual((result["bars"],result["expected"]),(36,36))
        self.assertEqual((result["high"],result["low"],result["close"]),(110,90,100))
        self.assertEqual(asia_range_reading({
            **result, "missing_times":[]})["status"],"VALIDO")

    def test_if_15m_incomplete_30m_can_certify(self):
        with patch("yfinance.download",
                   side_effect=[candles(15,missing_at=(5,0)),candles(30)]) as get:
            result=_complete_higher_timeframe(DAY,self.five)
        self.assertEqual(get.call_count,2)
        self.assertEqual(result["resolution"],"30m")
        self.assertEqual((result["bars"],result["expected"]),(18,18))

    def test_no_complete_timeframe_is_never_certified(self):
        with patch("yfinance.download",side_effect=[
                candles(15,missing_at=(5,0)),
                candles(30,missing_at=(5,0))]):
            result=_complete_higher_timeframe(DAY,self.five)
        self.assertIsNone(result)

    def test_inconsistent_close_is_not_accepted(self):
        alternate=candles(15)
        alternate.loc[alternate.index[-1],"Close"]=101.0
        with patch("yfinance.download",side_effect=[alternate,
             candles(30,missing_at=(5,0))]):
            self.assertIsNone(_complete_higher_timeframe(DAY,self.five))


if __name__=="__main__":
    unittest.main()
