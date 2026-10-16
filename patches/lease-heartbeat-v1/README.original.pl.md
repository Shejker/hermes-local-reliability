# Hermes Lease Heartbeat v1 — macOS / Desktop

**Cel:** ograniczyć opóźnienie odnowienia `session turn lease` wskutek kolejki do wspólnego mutexu `SessionDB._lock`. Poprawka nie gwarantuje nieprzerwanej pracy w każdej sytuacji. Jeżeli blokada SQLite, uśpienie komputera lub zastąpienie właściciela rzeczywiście uniemożliwi odnowienie w czasie TTL, Hermes nadal ma przerwać turę (fail-closed).

## Kontekst

Źródła z użytkownika: Hermes Agent v0.21.7, commit `71d34bad1045`, plus już zainstalowany **Preflight Recovery v2**. Paczka została przygotowana na podstawie 12 źródeł przekazanych 2026-10-15. Nie aktualizuje ich z GitHub.

## Zmiany

1. `hermes_state.py`: opcjonalny, ograniczony termin na nabycie *wewnętrznego mutexu Pythona* tylko dla wywołań, które jawnie o to proszą (`thread_lock_timeout_s`). Zwykłe zapisy zachowują stare działanie, wszystkie transakcje nadal wykorzystują oryginalny `BEGIN IMMEDIATE`.
2. `hermes_state_compression.py`: `refresh_session_turn_lease` korzysta z limitu 2 s na każde oczekiwanie na mutex i **istniejącego** całkowitego limitu SQLite `patience_s` (maksymalnie 20 s z agenta). Po chwilowej blokadzie ponawia próbę w limicie, bez otwierania nowych połączeń SQLite i bez zmiany właściciela.
3. `agent/turn_facade_lease.py`: domyślny okres odnawiania lease skrócony z 60 do 30 s (nadpisanie przez `_session_turn_lease_refresh_interval` nadal działa). TTL nadal 300 s. Brak zmian w logice ochronnego Stop po rzeczywistej utracie uprawnienia.

Nie zmienia: `state.db`, `config.yaml`, Guard v2.2, `conversation_loop.py`, Preflight Recovery v2, `context_compressor.py`, Telegram, modeli Ollama, progu 40 960.

## Bezpieczne uruchomienie

**Gdy Hermes jeszcze pracuje:** można uruchomić tylko testy i `--check`.

```bash
cd ~/Downloads/hermes-lease-heartbeat-v1
python3 tests/test_lease.py
python3 tests/test_install.py
python3 install.py --check
```

**Instalacja po zakończeniu tury:** całkowicie zamknij Hermes Desktop i upewnij się, że proces backendu zniknął. Potem:

```bash
python3 install.py --install
```

Instalator odmówi, jeżeli proces Hermesa nadal działa, sygnatury plików różnią się od otrzymanych źródeł lub wykryje wcześniejszą instalację. Oryginalne pliki są zapisywane do `~/.hermes/patch-backups/lease-heartbeat-v1`, a każda podmiana jest atomowa na poziomie jednego pliku; przy błędzie pozostałe pliki są cofane.

Po uruchomieniu aplikacji obserwuj logi przez 2–3 godziny:

```bash
tail -F ~/.hermes/logs/agent.log | grep -Ei --line-buffered 'session turn lease|lost session turn lease|refresh hit a SQLite lock|failed to refresh|Turn ended|runway'
```

Gdy pojawi się `session turn lease lost`, **nie usuwaj lease z bazy**. Zbierz logi, sprawdź ewentualny sleep/restart. `--check` oraz testy nie ingerują w bazę.

## Cofnięcie

Po całkowitym zamknięciu Hermes Desktop:

```bash
cd ~/Downloads/hermes-lease-heartbeat-v1
python3 install.py --rollback
```

Skrypt sprawdza hash plików i kopii oryginałów, nie cofa nieznanych lokalnych edycji i pozostawia Recovery v2 bez zmian.

## Ograniczenia

- To **wzmocnienie mechanizmu odnowienia**, nie potwierdzona naprawa wszystkich źródeł `session turn lease lost`. Nie rozwiąże faktycznego wygaśnięcia lease przy wielominutowym uśpieniu macOS ani permanentnie zakleszczonego zapisu.
- Testy są jednostkowe i integracyjne na osobnej pamięciowej SQLite; nie odtwarzają pełnego obciążenia produkcyjnej bazy ani kilkudniowej sesji.
- Nie należy uruchamiać instalacji w czasie aktywnego zadania, nawet jeśli sam skrypt ma ochronę przed działającą aplikacją.
