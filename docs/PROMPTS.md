# Prompts de démarrage — Lakmus v0.1

Chaque jalon se lance dans une nouvelle session Claude Code (ou un worktree dédié). Copier le prompt tel quel. Chaque prompt suit la règle du projet : rationale et architecture avant le code, validation de JIJI avant l'implémentation.

## Ordre et parallélisme

```text
M0 Socle ──┬── M1 Suite ─────┐
           ├── M2 Résultats ─┤
           ├── M3 Cibles ────┼── M5 Moteur ── M6 CLI run + rapport ── M7 Suite phare ── M8 Release 0.1.0
           └── M4 Scorers ───┘
```

M1 à M4 sont indépendants : ils peuvent tourner en parallèle dans des worktrees séparés, chacun sur sa branche. M3 peut même être découpé par adaptateur.

---

## M0 — Socle du dépôt

```text
Lis CLAUDE.md, docs/architecture/ARCHITECTURE.md, docs/spec/suite-format.md et les ADR.

Objectif M0 : un squelette de paquet qui s'installe, une CLI `lakmus --version`, et `make check` vert en CI.

1. Commence par me donner ton avis sur l'ADR 0003 (moteur maison vs harnais existant) en 5 lignes, puis attends ma décision avant de toucher au code.
2. Ensuite : crée l'arborescence src/lakmus/ décrite dans CLAUDE.md avec des modules vides typés, `errors.py`, la commande `--version`, un test, et vérifie `make check`.
Pas d'autre fonctionnalité.
```

## M1 — Chargement et validation des suites

```text
Jalon M1 : modèles Pydantic du manifeste (format 0.1), chargement d'une suite depuis un chemin, hash SHA-256 de la suite, commandes `lakmus validate` et `lakmus init`.

Utilise le skill architecture-first : écris l'ADR avec les modèles exacts, le schéma JSON généré, la liste des erreurs de validation et leurs messages. Arrête-toi pour validation.
Après validation : tests d'abord (fixtures dans tests/fixtures/suites/, une suite valide et une par règle de validation violée), puis implémentation. Termine par make check et une revue code-reviewer.
```

## M2 — Modèle de résultats et agrégation

```text
Jalon M2 : modèle RunResult (run.json format 0.1), agrégation par métrique, IC95 par bootstrap (1 000 rééchantillonnages, graine fixée), valeurs par groupe, verdict par seuil, génération du JSON Schema.

ADR d'abord (champs exacts, règles de comparabilité, gestion des métriques non déterministes), validation, puis tests table-driven et implémentation. Aucune dépendance numérique lourde : la librairie standard suffit.
```

## M3 — Protocole Target et adaptateurs

```text
Jalon M3 : protocole Target, TargetFingerprint, TargetOutput, registre par entry points, et les adaptateurs openai-compatible, openai, anthropic, ollama.

Applique le skill target-adapter. ADR d'abord : interface exacte, mapping des erreurs HTTP vers les exceptions du domaine, format du fichier pricing.toml, syntaxe --target. Validation, puis un adaptateur à la fois, avec tests respx (succès, 429, 500, timeout, réponse invalide, clé absente) et un test live par adaptateur.
```

## M4 — Scorers intégrés

```text
Jalon M4 : décorateur @scorer, chargement des scorers de suite (module:fonction), et les scorers intégrés exact, regex, json_schema, json_field_match, precision_recall_f1, pair_gap, pair_flip.

Applique le skill scorer. ADR d'abord (signature, métadonnées higher_is_better/deterministic, chargement sécurisé du code de suite avec confirmation), validation, puis tests table-driven avec du texte français accentué.
```

## M5 — Moteur d'exécution

```text
Jalon M5 : le moteur qui relie suite, cibles, scorers et résultats. Rendu Jinja2 sandboxé, concurrence bornée, timeouts, retries avec backoff et jitter, cache local, répétitions.

ADR d'abord : séquence exacte, clé de cache, comportement à l'interruption et à la reprise, gestion des erreurs par cas. Délègue la conception à l'agent architect si plusieurs options se valent. Après validation : tests avec une cible factice déterministe (pas de réseau), puis implémentation. Revue code-reviewer obligatoire.
```

## M6 — Commande run et rapport

```text
Jalon M6 : `lakmus run <suite> --target <cible>`, écriture de run.json + cases.jsonl.gz + report.html, codes de sortie 0/1/2/3, `lakmus compare` et `lakmus replay`.

ADR court pour le format du rapport et les options CLI, validation, puis implémentation. Le rapport HTML est un fichier unique sans dépendance externe, lisible hors ligne. Teste de bout en bout avec la cible factice.
```

## M7 — Suite phare : équité du tri de CV en français

```text
Jalon M7 : la suite suites/rh/equite-tri-cv-fr.

Applique les skills pair-testing et suite-authoring. Commence par me proposer la méthode (nombre de CV de base, postes, variables testées, sources documentées pour les valeurs, nombre de paires, métriques, seuils) et attends ma validation. Ensuite : générateur de paires reproductible avec test qui vérifie que A et B ne diffèrent que par la variable, README complet, lancement sur un modèle local, puis revue par l'agent suite-reviewer.
```

## M8 — Release 0.1.0

```text
Prépare la release 0.1.0 avec le skill release : vérifications, CHANGELOG, version, tag local. Ne pousse rien : montre-moi le changelog et les commandes.
```
