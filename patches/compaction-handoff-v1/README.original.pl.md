# Hermes v0.21.7 — poprawka przekazania aktywnego zadania po kompresji

**Cel:** naprawić niespójność w `agent/context_compressor.py`, w `_reappend_inflight_user_task`.

Oryginalny warunek sprawdza treść po **pierwszym** `_SUMMARY_END_MARKER`, choć `_has_merged_inflight_replay` poprawnie analizuje **ostatni** marker. Kiedy najnowszy handoff zawiera wcześniejszy handoff, starsza treść może błędnie zostać uznana za aktualną instrukcję. Poprawka zastępuje dokładnie jedną linię:

```diff
- if carrier_text.split(_SUMMARY_END_MARKER, 1)[1].strip():
+ if carrier_text.rsplit(_SUMMARY_END_MARKER, 1)[1].strip():
```

To nie jest gwarancja, że Hermes zawsze ukończy długie zadania — model może zakończyć przebieg `finish_reason=stop` niezależnie od kompresji. Nie usuwa również historycznych znaczników z bazy i nie zmienia Guard v2.1.

## Instalacja

1. Rozpakuj ZIP w `~/Downloads/hermes-compaction-handoff-fix-v1/`.
2. Najpierw sprawdź zgodność:

```bash
cd ~/Downloads/hermes-compaction-handoff-fix-v1
python3 patch_hermes.py --check
python3 test_patch.py
```

3. Zamknij Hermes Desktop normalnie; upewnij się, że żaden agent nie pracuje, i zastosuj zmianę:

```bash
python3 patch_hermes.py --apply
cd ~/.hermes/hermes-agent
git diff -- agent/context_compressor.py
```

4. Otwórz Hermes Desktop ponownie. Poprawka ładowana jest przy starcie backendu; sam zapis pliku nie zmienia już działającego procesu.

## Cofnięcie

```bash
python3 ~/Downloads/hermes-compaction-handoff-fix-v1/patch_hermes.py --rollback
```

Cofnięcie wymaga, żeby plik nadal miał SHA-256 zapisany podczas instalacji poprawki. Nie nadpisuje innych zmian ani nie dotyka `state.db`.

## Zabezpieczenia

- Wymaga commita `71d34bad10...` i czystego Git.
- Sprawdza AST i trzy funkcje oraz dokładnie jedno miejsce docelowej zmiany.
- Zachowuje kopię oryginału w `~/.hermes/patch-backups/hermes-compaction-handoff-v1/...`.
- Weryfikuje nowy plik po zapisie i pozwala na rollback.
- Nie ingeruje w istniejące wiadomości, znaczniki `STILL IN PROGRESS`, `OUT-OF-BAND`, modele, GUI, konfigurację Hermesa ani Guard v2.1.
- Testy obejmują jeden znacznik, dwa zagnieżdżone znaczniki, nowy aktywny replay oraz regresję starego warunku.
