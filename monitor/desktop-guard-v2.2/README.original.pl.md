# Hermes Desktop Guard v2.2 — bezpieczny tryb monitorowania

Przygotowano na podstawie dostarczonego `desktop_guard.py` v2.1, SHA-256:
`c14482e7adbaea850328fa7f87cb0e4da94ce1880a7c8aa463406027e208b794`.

## Dlaczego ta wersja różni się od wcześniejszej

W nocy 14 października v2.1 wysłał `session.interrupt` **w trakcie kompresji wykonywanej przez Hermesa**, co spowodowało utratę aktywnej tury. Późniejsza ratunkowa kompresja powiodła się po ~368 s, ale RPC Guard v2.1 zdążyło zgłosić timeout po 360 s, co wyłączyło dalszą kontrolę wyniku i auto-kontynuację.

Dodatkowo w v2.1 `auto_interrupt=false` **nie blokowało** automatycznej kompresji w stanie `idle` ani dalszych faz wcześniej zainicjowanej interwencji.

**v2.2 usuwa całkowicie tę klasę zagrożeń, ale ceną jest brak automatycznego odzyskiwania sesji** — to świadomy kompromis bezpieczeństwa. Takie odzyskiwanie wymaga po stronie Hermesa niezawodnego, aktualnego stanu trwającej kompresji, a także bezpiecznego potwierdzania wyników RPC, zanim zostanie dopuszczone. Obecne API i logi nie są wystarczająco mocną podstawą do gwarancji, że nigdy nie przerwiemy pracującego modelu.

Wersja v2.2:

- Odczytuje `session.active_list`, `session.usage`, `session.context_breakdown` i ostrzega przy progach z `policy.json`.
- **Nie wywołuje** `session.interrupt`, `session.compress`, `prompt.submit`, nie uruchamia procesów Hermesa, nie modyfikuje `state.db`.
- Nawet jeśli istnieją stany `ready_to_compress` / `ready_to_continue` z v2.1, nie wykona ich.
- Jeśli w polityce przez pomyłkę ustawiono `auto_interrupt=true` lub `auto_continue=true`, ignoruje te żądania i ostrzega.
- Rozpoznaje z logów ostatni *zaobserwowany* początek kompresji (`compression_log`), ale **nie** traktuje tego jako niezawodnego wskaźnika wykonania.
- Wykrywa niezmieniony przez 10 minut odczyt kontekstu. Nie nazywa tego zawieszeniem, ponieważ dostawca może długo nie aktualizować licznika podczas generowania.
- Działa z istniejącym LaunchAgent `com.local.hermes-desktop-guard`, uruchamianym co 30 s, oraz istniejącym `policy.json`.

## Instalacja (macOS)

1. Rozpakuj archiwum ZIP. Jeżeli Hermes pracuje, nie musisz go wyłączać. Instalator nie dotyka procesu Hermesa, ale odmawia zapisu, gdy obecny cykl Guard nadal trzyma plik `guard.lock`.
2. W Terminalu:

```bash
cd ~/Downloads/hermes-desktop-guard-v2.2
python3 install_guard.py --check
python3 test_guard.py
python3 install_guard.py --install
```

Instalator wymaga dokładnie znanej wersji pliku v2.1, zakłada kopię zapasową w `~/.hermes/desktop-guard/source-backups-v22/`, podmienia plik atomowo, zachowuje uprawnienia. `config.yaml`, `state.db`, `policy.json`, modele i Hermes Desktop pozostają bez zmian.

Nowa wersja zaczyna działać przy **kolejnym uruchomieniu cyklu LaunchAgent**, zazwyczaj w ciągu 30 sekund.

Sprawdzenie:

```bash
~/.hermes/desktop-guard/venv/bin/python3 ~/.hermes/desktop-guard/desktop_guard.py --version
~/.hermes/desktop-guard/venv/bin/python3 ~/.hermes/desktop-guard/desktop_guard.py --check
tail -12 ~/.hermes/desktop-guard/guard.log
cat ~/.hermes/desktop-guard/policy.json
```

Szukaj `v2.2 mode=observe_only` i `phase=monitor_only`. W `policy.json` zachowaj `auto_interrupt=false` i `auto_continue=false`, chociaż v2.2 ignoruje również ustawienia `true`.

## Cofnięcie

Gdy Guard nie jest w trakcie długiego cyklu:

```bash
cd ~/Downloads/hermes-desktop-guard-v2.2
python3 install_guard.py --rollback
```

**Uwaga:** cofnięty v2.1 może nadal uruchamiać automatyczną kompresję sesji `idle` nawet przy `auto_interrupt=false`. Nie przywracaj v2.1 do bezobsługowego działania bez dodatkowego zabezpieczenia. Instalator cofania nie zmienia polityki.

## Czego v2.2 nie obiecuje

Nie zapewnia pełnej bezobsługowej pracy 24/7 i nie rozwiązuje błędu Hermesa `session_turn_lease_lost`. Przy kontekście ponad 85–90% pozostają tylko powiadomienia i diagnostyka. Aby bezpiecznie przywrócić automatyczne przerwanie/wznowienie, trzeba dodać do **samego backendu Hermesa** wiarygodny stan kompaktowania oraz protokół potwierdzania zakończenia operacji; zewnętrzny watchdog nie może tego zagwarantować na podstawie samych próbek `session.usage`.
