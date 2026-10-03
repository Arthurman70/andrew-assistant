"""Weekly alarm calendar arithmetic, using the host's local wall clock."""
import datetime as dt
import json
import re

DAYS=('monday','tuesday','wednesday','thursday','friday','saturday','sunday')


def repeat_clause(text):
    """Return clock/selector text, weekdays, and whether repetition was specified."""
    once=re.search(r'\s+\b(?:once|one time|one-time|without repeating)$',text,re.I)
    if once:return text[:once.start()].strip(),[],True
    match=re.search(r'\s*\b(?:every|each|on)\s+(.+)$',text,re.I)
    if not match:return text.strip(),[],False
    phrase=match[1].lower().strip().rstrip('.')
    phrase=re.sub(r'^(?:week on\s+|week\b,?\s+)','',phrase)
    if phrase in ('day','daily','single day','day of the week','day for a whole week','day of a whole week'):
        days=list(range(7))
    elif phrase in ('weekday','weekdays','week day','weekdays of the week'):days=list(range(5))
    elif phrase in ('weekend','weekends','weekend day'):days=[5,6]
    else:
        days=[]
        for token in re.split(r'\s*(?:,|\band\b)\s*',phrase):
            token=token.strip().rstrip('s')
            if token not in DAYS:raise ValueError('Choose every day, every weekday, every weekend, or named days such as every Monday and Friday.')
            days.append(DAYS.index(token))
    return text[:match.start()].strip(),sorted(set(days)),True


def weekdays(row):
    value=row.get('repeat_days')
    return json.loads(value) if value else []


def repeat_label(row):
    days=weekdays(row)
    if not days:return 'One time'
    if days==list(range(7)):return 'Every day'
    if days==list(range(5)):return 'Weekdays'
    if days==[5,6]:return 'Weekends'
    return 'Every '+', '.join(DAYS[i].title() for i in days)


def next_occurrence(clock, days, after):
    """Choose a future calendar day, never add a fixed 86400-second day.

    timestamp() applies the host timezone/DST for each candidate. Nonexistent
    spring-forward times move forward through the gap; fall-back rings once.
    """
    hour,minute=map(int,clock.split(':'))
    if not days or any(type(i) is not int or not 0<=i<=6 for i in days):
        raise ValueError('Choose at least one valid weekday.')
    start=dt.datetime.fromtimestamp(after).date()
    for offset in range(8):
        date=start+dt.timedelta(days=offset)
        if date.weekday() in days:
            target=dt.datetime.combine(date,dt.time(hour,minute)).timestamp()
            if target>after:return target
    raise ValueError('Could not find the next alarm day.')


def tick(app, now):
    """Ring each occurrence once and retain the next regular alarm while snoozed."""
    with app.lock,app.db:
        rows=[dict(r) for r in app.db.execute(
            "SELECT * FROM timers WHERE (status='active' AND (due<=? OR next_due<=?)) OR "
            "(kind='alarm' AND status='ringing' AND next_due<=?)",(now,now,now))]
        for row in rows:
            upcoming=None
            days=weekdays(row)
            if row['kind']=='alarm' and days:
                upcoming=row['next_due']
                if not upcoming or upcoming<=now:
                    upcoming=next_occurrence(row['alarm_time'],days,now)
            app.db.execute("UPDATE timers SET status='ringing',next_due=? WHERE id=?",(upcoming,row['id']))
        return rows
