import sqlite3, json, glob, os, sys

# Usage: python3 check_ledger.py <dir-with-sql-dumps> [<dir2> ...]
# Scans every matches_with_stats.sql dump passed in and tries to resolve any
# "unresolved" pick in docs/1x-picks-ledger.json by finding the home team's
# result against that away team elsewhere in the corpus (self-leak or as
# someone else's history row). Does NOT write the ledger - print the results
# and update docs/1x-picks-ledger.json by hand once confirmed.

if len(sys.argv) < 2:
    print("Usage: python3 check_ledger.py <dir-with-sql-dumps> [<dir2> ...]")
    sys.exit(1)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ledger = json.load(open(os.path.join(REPO_ROOT, 'docs', '1x-picks-ledger.json')))
picks = ledger['picks']

cols = ['fixture_id','kickoff','competition','team1','team2','side_flag','team','hist_id','hist_date',
        'hist_status','hist_competition','hist_side','hist_opponent','goals_for','goals_against',
        'xg','possession','shots_total','shots_on_target','big_chances','corners','fouls','offsides',
        'yellow_cards','red_cards','passes_completed','passes_total','pass_accuracy']

conn = sqlite3.connect(':memory:')
c = conn.cursor()
c.execute(f"CREATE TABLE rows ({','.join(cols)}, src TEXT)")
conn.commit()
paths = []
for d in sys.argv[1:]:
    paths.extend(glob.glob(os.path.join(d, '*.sql')))
for path in sorted(paths):
    sql = open(path, encoding='utf-8').read()
    src = os.path.basename(path)
    c.execute(f"CREATE TEMP TABLE tmp ({','.join(cols)})")
    sql2 = sql.replace('upcoming_matches_team_stats', 'tmp')
    c.executescript(sql2)
    c.execute(f"INSERT INTO rows SELECT {','.join(cols)}, ? FROM tmp", (src,))
    c.execute("DROP TABLE tmp")
    conn.commit()

found_any = False
for pick in picks:
    if pick['status'] != 'unresolved':
        continue
    home, away = pick['home'], pick['away']

    # Find this fixture's own kickoff date wherever it appears as an upcoming
    # fixture (team1=home, team2=away) - used as the cutoff so we never
    # mistake a PAST head-to-head (already part of the pick's own history,
    # e.g. an earlier leg) for the pick's actual outcome.
    c.execute("""
        SELECT MIN(kickoff) FROM rows WHERE team1=? AND team2=?
    """, (home, away))
    kickoff = c.fetchone()[0]
    if kickoff is None:
        kickoff = pick.get('kickoff')  # fall back to stored value if we have it
    if kickoff is None:
        print(f"[SKIP] {home} vs {away}: fixture kickoff not found in any dump, cannot safely date-filter, skipping")
        continue

    # Strategy 1: this exact fixture (home,away) shows up self-leaked (hist_id=fixture_id) in ANY dump
    c.execute("""
        SELECT src, goals_for, goals_against FROM rows
        WHERE team1=? AND team2=? AND hist_id=fixture_id AND side_flag='team1_home' AND team=?
    """, (home, away, home))
    r = c.fetchone()
    if r:
        print(f"[SELF-LEAK] {home} {r[1]}:{r[2]} {away}  (src={r[0]}, kickoff={kickoff})")
        found_any = True
        continue
    # Strategy 2: home team appears as 'team' with hist_opponent=away, hist_side='home',
    # in ANY dump's history rows, STRICTLY AFTER this fixture's own kickoff
    # (otherwise it's just an earlier leg/H2H already baked into the pick's own stats).
    c.execute("""
        SELECT DISTINCT hist_date, goals_for, goals_against, src FROM rows
        WHERE team=? AND hist_opponent=? AND hist_side='home' AND goals_for IS NOT NULL
              AND hist_date >= ?
        ORDER BY hist_date ASC LIMIT 3
    """, (home, away, kickoff))
    rows2 = c.fetchall()
    if rows2:
        print(f"[HIST-ROW] {home} vs {away} (kickoff={kickoff}) resolved after kickoff: {rows2}")
        found_any = True
        continue

if not found_any:
    print("No new resolutions found for any unresolved pick.")
