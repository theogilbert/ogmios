# Long

Made-up notes: subjects that come back over many days.

## 2022-03-10
- Debugged a timezone bug in the export job

## 2022-03-15
- Lunch and learn on data contracts
- Revue de code sur ingestion-api, deux remarques sur les retries
- Investigated slow queries on the reporting database

## 2022-03-17
- Nettoyage des buckets temporaires à la main
- Backfill of the invoices table for March

## 2022-03-21
- Point hebdo avec l'équipe data, rien de bloquant

## 2022-04-05
- Wrote a runbook for the export API restarts

## 2022-04-13
- Passage en revue des accès GitLab
- Secrets SSH déplacés dans Vault, fin des clés sur les workers

## 2022-04-21
- Added integration tests for the partner connector

## 2022-04-22
- Formation interne sur les bonnes pratiques SQL

## 2022-05-11
- Tuned the Airflow pool sizes after the slowdown
- Lu un article sur Terraform, à essayer un jour pour le réseau
- Investigated slow queries on the reporting database

## 2022-05-24
- Refonte du dashboard de suivi des coûts
- Atelier découverte avec le nouveau partenaire logistique
- Prometheus alert on the disk usage of the workers

## 2022-06-13
- Préparation de la démo de fin de sprint

## 2022-06-20
- Refonte du dashboard de suivi des coûts
- Sprint planning, 18 points pris
- Julie suggests we should look at Terraform for the GCP projects, nothing decided

## 2022-06-23
- Updated the README of the ingestion-api repo

## 2022-06-29
- Formation interne sur les bonnes pratiques SQL

## 2022-07-07
- Lunch and learn on data contracts

## 2022-08-02
- Fixed a flaky test in the reporting-api pipeline

## 2022-08-15
- Atelier découverte avec le nouveau partenaire logistique
- Réunion budget cloud, BigQuery reste le premier poste

## 2022-08-22
- Backfill of the invoices table for March
- Wrote a runbook for the export API restarts
- Vault token rotation automated with a nightly job

## 2022-08-26
- Préparation de la démo de fin de sprint
- Wrote a runbook for the export API restarts

## 2022-08-29
- Analyse d'un pic de latence sur l'API d'export

## 2022-09-02
- Migration du job de purge vers Airflow 2
- Sprint planning, 18 points pris
- Réunion budget cloud, BigQuery reste le premier poste

## 2022-09-05
- Lunch and learn on data contracts
- Rotation d'astreinte, nuit calme
- Formation interne sur les bonnes pratiques SQL

## 2022-09-09
- Replaced the cron on srv-batch01 with a DAG

## 2022-09-12
- Point hebdo avec l'équipe data, rien de bloquant
- terraform apply sur ingestion-api : buckets et service accounts désormais gérés en code

## 2022-09-27
- 1:1 with my manager, talked about priorities for the quarter
- Reviewed the security questionnaire for the audit
- Analyse d'un pic de latence sur l'API d'export

## 2022-09-29
- Analyse d'un pic de latence sur l'API d'export
- Imported the composer-prod bucket into the Terraform state
- Prometheus alert on the disk usage of the workers

## 2022-10-05
- Support aux analystes sur une requête BigQuery trop coûteuse
- Reviewed the security questionnaire for the audit

## 2022-10-12
- Revue de code sur ingestion-api, deux remarques sur les retries

## 2022-10-17
- Module terraform pour les service accounts, revue avec Hugo
- Added integration tests for the partner connector
- Revue trimestrielle des objectifs

## 2022-11-08
- terraform plan was failing on the IAM bindings, fixed the provider version
- Mise à jour des dépendances Python sur ingestion-api
- Réunion budget cloud, BigQuery reste le premier poste

## 2022-11-11
- Mise à jour des dépendances Python sur ingestion-api

## 2022-11-14
- Nettoyage des buckets temporaires à la main
- Replaced the cron on srv-batch01 with a DAG

## 2022-12-05
- Rédaction du compte rendu d'incident du week-end
- Passage de reporting-api sous terraform, plan propre

## 2022-12-12
- Reviewed the security questionnaire for the audit
- Reduced the log volume of the ingestion pods

## 2023-01-18
- Correction du parsing des dates dans le connecteur partenaire
- Wrote a runbook for the export API restarts

## 2023-01-23
- Moved the terraform state to a GCS backend with locking
- Rotation d'astreinte, nuit calme
- Atelier découverte avec le nouveau partenaire logistique

## 2023-02-06
- Discussion café : Kafka serait peut-être utile pour les événements partenaires
- Reviewed the security questionnaire for the audit

## 2023-02-23
- Correction du parsing des dates dans le connecteur partenaire

## 2023-02-27
- Réunion budget cloud, BigQuery reste le premier poste
- Atelier découverte avec le nouveau partenaire logistique
- Préparation de la démo de fin de sprint
- Atelier terraform avec l'équipe plateforme, conventions de nommage

## 2023-02-28
- Debugged a timezone bug in the export job
- Migration du job de purge vers Airflow 2
- Mise à jour des dépendances Python sur ingestion-api

## 2023-03-13
- Read the Kafka vs PubSub comparison from the platform team, no decision
- Mise à jour des dépendances Python sur ingestion-api

## 2023-03-20
- Terraform drift on the firewall rules, reapplied after review
- Migration du job de purge vers Airflow 2
- Nettoyage des buckets temporaires à la main

## 2023-04-21
- Grafana dashboard for the SLO of the export API
- Migration du job de purge vers Airflow 2

## 2023-04-24
- Added terraform validate and tflint to the CI of terraform-network
- Analyse d'un pic de latence sur l'API d'export

## 2023-04-25
- Prometheus alert on the disk usage of the workers

## 2023-05-15
- Nettoyage des buckets temporaires à la main
- Cleaned up old DAGs in composer-prod
- Déployé Kafka en production pour le flux d'événements partenaires, 3 brokers
- Analyse d'un pic de latence sur l'API d'export

## 2023-05-26
- Analyse d'un pic de latence sur l'API d'export

## 2023-05-31
- Added retries to the SFTP polling job
- Passage en revue des accès GitLab
- Reduced the log volume of the ingestion pods

## 2023-06-05
- Refonte des modules terraform réseau, découpage par environnement
- Revue de code sur ingestion-api, deux remarques sur les retries
- 1:1 with my manager, talked about priorities for the quarter

## 2023-06-06
- Analyse d'un pic de latence sur l'API d'export

## 2023-06-14
- Added retries to the SFTP polling job
- Préparation de la démo de fin de sprint
- Point hebdo avec l'équipe data, rien de bloquant

## 2023-06-26
- Reduced the log volume of the ingestion pods
- Kafka consumer lag alert added to Grafana

## 2023-06-30
- Added retries to the SFTP polling job

## 2023-07-03
- Replaced the cron on srv-batch01 with a DAG

## 2023-07-06
- Échange avec la sécu sur les plages IP sources
- Réunion budget cloud, BigQuery reste le premier poste
- Refonte du dashboard de suivi des coûts

## 2023-07-10
- 1:1 with my manager, talked about priorities for the quarter
- Upgraded terraform from 1.3 to 1.5 on all repos
- Revue trimestrielle des objectifs
- Added integration tests for the partner connector

## 2023-07-12
- Wrote a runbook for the export API restarts
- Échange avec la sécu sur les plages IP sources

## 2023-07-14
- Investigated slow queries on the reporting database
- Fixed a flaky test in the reporting-api pipeline
- Échange avec la sécu sur les plages IP sources

## 2023-08-01
- Échange avec la sécu sur les plages IP sources
- Sprint planning, 18 points pris

## 2023-08-07
- Added retries to the SFTP polling job
- Rebalancing des partitions Kafka après ajout d'un broker
- Updated the README of the ingestion-api repo
- Atelier découverte avec le nouveau partenaire logistique

## 2023-08-14
- Grafana dashboard for the SLO of the export API
- Answered analysts' questions on the partner_files_ingest schedule

## 2023-08-16
- Lunch and learn on data contracts
- Support aux analystes sur une requête BigQuery trop coûteuse
- Prometheus alert on the disk usage of the workers

## 2023-09-04
- Terraform : import des règles firewall de prod, 40 ressources
- Added integration tests for the partner connector

## 2023-09-14
- Updated the README of the ingestion-api repo
- Answered analysts' questions on the partner_files_ingest schedule
- Déploiement de la nouvelle version de l'API de reporting

## 2023-09-18
- Fixed the kafka consumer offset reset on partner-events
- Mise à jour de la documentation du runbook SFTP
- Support aux analystes sur une requête BigQuery trop coûteuse
- 1:1 with my manager, talked about priorities for the quarter

## 2023-09-22
- Support aux analystes sur une requête BigQuery trop coûteuse
- Mise à jour des dépendances Python sur ingestion-api
- Réunion budget cloud, BigQuery reste le premier poste

## 2023-10-16
- Wrote the terraform onboarding doc for the analysts team
- Investigated slow queries on the reporting database

## 2023-10-18
- Refonte du dashboard de suivi des coûts

## 2023-10-26
- Fixed a flaky test in the reporting-api pipeline
- Analyse d'un pic de latence sur l'API d'export

## 2023-10-30
- Reduced the log volume of the ingestion pods

## 2023-11-01
- Reduced the log volume of the ingestion pods
- Support aux analystes sur une requête BigQuery trop coûteuse
- Migration du job de purge vers Airflow 2

## 2023-11-02
- Déploiement de la nouvelle version de l'API de reporting
- Cleaned up old DAGs in composer-prod

## 2023-11-20
- terraform destroy du bac à sable partner-sandbox, économie de 200 euros par mois
- Updated the README of the ingestion-api repo

## 2023-11-23
- Grafana dashboard for the SLO of the export API
- Passage en revue des accès GitLab

## 2023-12-04
- Upgrade Kafka 3.4 vers 3.6 en recette
- Atelier découverte avec le nouveau partenaire logistique

## 2023-12-18
- Revue de code sur ingestion-api, deux remarques sur les retries
- Grafana dashboard for the SLO of the export API
- Préparation de la démo de fin de sprint

## 2024-01-01
- Helped the new intern set up their laptop
- Échange avec la sécu sur les plages IP sources
- Investigated slow queries on the reporting database

## 2024-01-09
- Prometheus alert on the disk usage of the workers
- Nettoyage des buckets temporaires à la main

## 2024-01-12
- Sprint planning, 18 points pris

## 2024-01-15
- Reduced the log volume of the ingestion pods
- Migrated the monitoring stack definitions to terraform

## 2024-01-29
- Reviewed the security questionnaire for the audit
- Nettoyage des buckets temporaires à la main
- Kafka 3.6 in prod, no incident

## 2024-01-31
- Refonte du dashboard de suivi des coûts

## 2024-02-08
- Réunion budget cloud, BigQuery reste le premier poste
- Mise à jour des dépendances Python sur ingestion-api

## 2024-02-09
- Prometheus alert on the disk usage of the workers
- Tuned the Airflow pool sizes after the slowdown

## 2024-02-19
- Revue de code sur ingestion-api, deux remarques sur les retries
- Revue de la PR terraform de Hugo sur les VPC peering
- Mise à jour de la documentation du runbook SFTP

## 2024-02-29
- Préparation de la démo de fin de sprint

## 2024-03-25
- Answered analysts' questions on the partner_files_ingest schedule
- Mise à jour de la documentation du runbook SFTP
- Fixed a flaky test in the reporting-api pipeline
- Rétention du topic partner-events passée à 7 jours sur Kafka

## 2024-03-29
- Réunion budget cloud, BigQuery reste le premier poste

## 2024-04-01
- Grafana dashboard for the SLO of the export API
- Rédaction du compte rendu d'incident du week-end
- Atelier découverte avec le nouveau partenaire logistique

## 2024-04-08
- Terraform module for the k8s node pools, first version merged
- Migration du job de purge vers Airflow 2

## 2024-04-11
- Rotation d'astreinte, nuit calme
- Reduced the log volume of the ingestion pods

## 2024-05-02
- Reduced the log volume of the ingestion pods
- Wrote a runbook for the export API restarts

## 2024-05-27
- Rotation d'astreinte, nuit calme
- Bug du provider google sur terraform, épinglé en 5.20

## 2024-05-28
- Helped the new intern set up their laptop

## 2024-06-05
- Mise à jour de la documentation du runbook SFTP
- Reduced the log volume of the ingestion pods

## 2024-06-17
- Revue de code sur ingestion-api, deux remarques sur les retries
- Schema registry added in front of Kafka topics
- Prometheus alert on the disk usage of the workers

## 2024-07-01
- Support aux analystes sur une requête BigQuery trop coûteuse
- Wrote a runbook for the export API restarts
- Terraform workspaces abandoned in favour of one directory per environment

## 2024-07-26
- Rotation d'astreinte, nuit calme
- Support aux analystes sur une requête BigQuery trop coûteuse

## 2024-08-12
- Incident Kafka : disque plein sur le broker 2, rétention réduite
- Nettoyage des buckets temporaires à la main
- Prometheus alert on the disk usage of the workers
- Fixed a flaky test in the reporting-api pipeline

## 2024-08-16
- Formation interne sur les bonnes pratiques SQL
- Prometheus alert on the disk usage of the workers
- Rotation d'astreinte, nuit calme

## 2024-08-19
- Backfill of the invoices table for March
- Answered analysts' questions on the partner_files_ingest schedule

## 2024-08-26
- Cleaned up old DAGs in composer-prod

## 2024-09-09
- Fixed a flaky test in the reporting-api pipeline
- Ajout d'Atlantis pour lancer terraform plan sur les merge requests
- Correction du parsing des dates dans le connecteur partenaire
- Debugged a timezone bug in the export job

## 2024-09-18
- Migration du job de purge vers Airflow 2
- Revue de code sur ingestion-api, deux remarques sur les retries
- Point hebdo avec l'équipe data, rien de bloquant

## 2024-10-03
- Mise à jour de la documentation du runbook SFTP
- Tuned the Airflow pool sizes after the slowdown
- Échange avec la sécu sur les plages IP sources

## 2024-10-11
- Replaced the cron on srv-batch01 with a DAG

## 2024-10-21
- terraform for the partner SFTP servers, replaces the manual setup
- Reduced the log volume of the ingestion pods

## 2024-10-29
- Fixed a flaky test in the reporting-api pipeline

## 2024-11-08
- Déploiement de la nouvelle version de l'API de reporting

## 2024-11-25
- Investigated slow queries on the reporting database
- Mise à jour des dépendances Python sur ingestion-api
- Sorted out the terraform state locks left by the failed Atlantis run

## 2024-12-09
- Échange avec la sécu sur les plages IP sources
- Migration du job de purge vers Airflow 2
- Kafka connect sink to BigQuery for the partner events
- Helped the new intern set up their laptop

## 2024-12-18
- Prometheus alert on the disk usage of the workers

## 2024-12-19
- Support aux analystes sur une requête BigQuery trop coûteuse
- Rédaction du compte rendu d'incident du week-end
- Refonte du dashboard de suivi des coûts
