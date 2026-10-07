import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
from core import Andrew,ROOT
from weather import Weather,plan,location_from,conditions

def fixture():
    dates=[dt.date(2026,10,7)+dt.timedelta(days=i) for i in range(16)]
    times=[dt.datetime(2026,10,7)+dt.timedelta(hours=i) for i in range(384)]
    return {'timezone':'Asia/Tokyo','current':{'time':'2026-10-07T14:15','temperature_2m':70,'apparent_temperature':68,'weather_code':2,'is_day':1,'relative_humidity_2m':55,'wind_speed_10m':8},
            'daily':{'time':[d.isoformat() for d in dates],'temperature_2m_max':[75+i for i in range(16)],'temperature_2m_min':[50+i for i in range(16)],'weather_code':[2]*16,'precipitation_probability_max':[20]*16,'wind_speed_10m_max':[8]*16},
            'hourly':{'time':[t.isoformat(timespec='minutes') for t in times],'temperature_2m':[60+i%24 for i in range(384)],'apparent_temperature':[58+i%24 for i in range(384)],'weather_code':[1]*384,'is_day':[int(6<=t.hour<18) for t in times],'precipitation_probability':[i%80 for i in range(384)],'wind_speed_10m':[8]*384}}

class WeatherTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'data/tests').mkdir(parents=True,exist_ok=True);self.temp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests');self.app=Andrew(self.temp.name);self.data=fixture();self.calls=[];self.time=1000
        self.app.daily.forecast=Weather(self.app,self.fetch,lambda:self.time);self.w=self.app.daily.forecast
    def tearDown(self):self.app.db.close();self.temp.cleanup()
    def fetch(self,url):
        self.calls.append(url)
        if 'geocoding' in url:return {'results':[{'name':'Tokyo','country':'Japan','latitude':35.6,'longitude':139.7,'timezone':'Asia/Tokyo'}]}
        return self.data
    def test_three_days_show_real_highs_lows_and_navigate_requesting_device(self):
        answer=self.app.command('weather in Tokyo for the next three days')
        view=self.w.state()['pc'];self.assertEqual(len(view['days']),3);self.assertEqual(view['days'][0]['high'],75);self.assertEqual(view['days'][2]['low'],52)
        self.assertIn('high 75',answer);self.assertIn('low 50',answer);self.assertEqual(self.app.controls.snapshot()['pc']['section'],'weatherPanel')
    def test_next_week_is_seven_days_and_keeps_city_out_of_period(self):
        self.app.command('show me the forecast in Tokyo for the next week');view=self.w.state()['pc']
        self.assertEqual(len(view['days']),7);self.assertEqual(view['city'],'Tokyo');self.assertEqual(parse_qs(urlsplit(self.calls[0]).query)['name'],['Tokyo'])
    def test_tomorrow_hourly_uses_location_date_and_contains_24_hour_steps(self):
        self.app.command('hourly weather in Tokyo for tomorrow');view=self.w.state()['pc']
        self.assertEqual(view['days'][0]['date'],'2026-10-08');self.assertEqual(len(view['hours']),24);self.assertEqual(view['hours'][0]['time'],'2026-10-08T00:00');self.assertEqual(view['timezone'],'Asia/Tokyo')
    def test_next_six_hours_start_at_current_local_hour(self):
        self.app.command('weather in Tokyo for the next six hours');hours=self.w.state()['pc']['hours']
        self.assertEqual(len(hours),6);self.assertEqual(hours[0]['time'],'2026-10-07T14:00');self.assertEqual(hours[-1]['time'],'2026-10-07T19:00')
    def test_hourly_followup_preserves_requested_week_and_reuses_cached_data(self):
        self.app.command('weather in Tokyo for the next week');self.app.command('break it down hour by hour')
        view=self.w.state()['pc'];self.assertEqual(view['mode'],'hourly');self.assertEqual(len(view['days']),7);self.assertEqual(len(self.calls),2)
    def test_units_followup_preserves_period_and_uses_provider_temperature_unit(self):
        self.app.command('weather in Tokyo for the next week');self.app.command('in Celsius')
        view=self.w.state()['pc'];self.assertEqual(view['units'],'celsius');self.assertEqual(len(view['days']),7)
        self.assertEqual(parse_qs(urlsplit(self.calls[-1]).query)['temperature_unit'],['celsius'])
    def test_period_can_precede_city_and_qualifiers_remain(self):
        self.assertEqual(location_from('weather for the next three days in Boston, Massachusetts'),'Boston, Massachusetts')
        self.assertEqual(location_from('weather in Grand Rapids, MI for the next week'),'Grand Rapids, MI')
        self.assertEqual(location_from('weather in Tokyo day after tomorrow'),'Tokyo')
        self.assertEqual(location_from('weather in Tokyo Friday'),'Tokyo')
        self.assertIsNone(location_from('weather in the next week'))
    def test_open_weather_and_daily_followup(self):
        self.assertIn('on screen',self.app.command('open weather'))
        self.app.command('weather in Tokyo for the next week hourly');self.app.command('show me daily')
        self.assertEqual(self.w.state()['pc']['mode'],'daily');self.assertEqual(len(self.w.state()['pc']['days']),7)
    def test_hour_ranges_cross_midnight_without_being_truncated_to_calendar_days(self):
        self.app.command('weather in Tokyo for the next 72 hours');view=self.w.state()['pc']
        self.assertEqual(len(view['hours']),72);self.assertEqual(len(view['days']),4)
    def test_selected_weekday_and_two_week_period(self):
        self.app.command('weather in Tokyo next Friday');self.assertEqual(self.w.state()['pc']['days'][0]['date'],'2026-10-09')
        self.app.command('weather for the next two weeks');self.assertEqual(len(self.w.state()['pc']['days']),14)
    def test_invalid_period_is_not_clipped_or_fabricated(self):
        with self.assertRaises(ValueError):plan('next 30 days')
        with self.assertRaises(ValueError):plan('next 0 days')
        with self.assertRaises(ValueError):self.w.request('Tokyo',detail='every second')
    def test_null_values_are_unavailable_not_zero_or_sunny(self):
        self.data['daily']['temperature_2m_min'][0]=None;self.data['current']['temperature_2m']=None;self.data['current']['weather_code']=None
        answer=self.app.command('weather in Tokyo');view=self.w.state()['pc']
        self.assertIsNone(view['days'][0]['low']);self.assertIn('unavailable',answer);self.assertEqual(view['current']['condition'],'Conditions unavailable')
    def test_night_clear_condition_uses_moon(self):self.assertEqual(conditions(0,False)['icon'],'🌙')
    def test_unrelated_pc_tasks_do_not_become_weather_requests(self):
        self.assertIsNone(self.w.route('use Claude to build a weather app','pc'));self.assertIsNone(self.w.route('tell Claude to compare weather APIs','pc'))
        self.app.command('weather in Tokyo');self.app.command('what time is it');self.assertIsNone(self.w.route('next week','pc'))
    def test_followup_is_source_and_identified_person_scoped(self):
        self.app.command('my name is Sam');self.app.command('weather in Tokyo')
        self.assertIsNone(self.w.route('next week','pi'));self.app.command('my name is Kim');self.assertIsNone(self.w.route('next week','pc'))
    def test_failed_refresh_keeps_original_location_and_marks_old_view(self):
        self.app.command('weather in Tokyo');self.time+=601
        with patch.object(self.w,'json',side_effect=OSError('offline')):answer=self.app.command('weather in Tokyo for next week')
        view=self.w.state()['pc'];self.assertIn('temporarily unavailable',answer);self.assertTrue(view['error']);self.assertEqual(view['location'],'Tokyo, Japan')
    def test_model_command_contract_routes_daily_and_hourly_forecasts(self):
        answer=self.app.actions.consume(json.dumps({'reply':'','commands':[{'name':'weather.forecast','args':{'city':'Tokyo','period':'next week','detail':'hourly'}}]}),'browser')
        self.assertIn('forecast',answer);self.assertEqual(len(self.w.state()['browser']['days']),7);self.assertNotIn('pc',self.w.state())
    def test_missing_location_prompts_once_without_network_call(self):
        self.assertIn('Tell me a city',self.app.command('weather for the next three days'));self.assertEqual(self.calls,[])
    def test_forecast_followup_defaults_to_previous_city(self):
        self.app.command('weather in Tokyo');self.app.command('what about the next three days')
        self.assertEqual(len(self.w.state()['pc']['days']),3);self.assertEqual(len(self.calls),2)

if __name__=='__main__':unittest.main()
