# Done

Notes quotidiennes / daily log.

## 2022-03-14
- Setup du poste, accès VPN OK
- Réunion d'onboarding avec l'équipe data
- Lecture de la doc Airflow interne (composer-prod)

## 2022-03-15
- Accès GitLab demandé à l'équipe IAM, ticket IAM-2231
- Premier DAG en local avec docker-compose

## 2022-03-17
- Revue de code sur le repo ingestion-api
- Bug sur le parsing des fichiers CSV des partenaires : encodage latin-1

## 2022-04-04
- Ouverture de flux demandée entre srv-sftp-part01 et le cluster Airflow (composer-prod), ticket RESEAU-4512
- Ports 22 depuis les workers Airflow vers le SFTP partenaire
- Relance prévue la semaine prochaine si pas de retour

## 2022-04-11
- Relance équipe réseau sur RESEAU-4512, toujours en attente de validation sécu
- Point avec Julie sur le planning du sprint

## 2022-04-19
- RESEAU-4512 validé par la sécu, mise en place prévue jeudi
- Rédaction du runbook pour la rotation des clés SSH du SFTP

## 2022-04-21
- Flux ouverts ! Test de connexion depuis un worker Airflow vers srv-sftp-part01 OK
- DAG partner_files_ingest déployé en recette

---

Mardi 3 janvier 2023

- Retour de congés, tri des mails
- Mise à jour de la stack Grafana en 10.2

Jeudi 5 janvier 2023

- Incident : le DAG partner_files_ingest a échoué toute la nuit, timeout SFTP
- Cause : rotation de la clé SSH côté partenaire non communiquée
- Nouvelle clé déployée dans le vault, DAG relancé

Lundi 9 janvier 2023

- Atelier sur la migration vers Kubernetes avec l'équipe plateforme
- Estimation : 3 sprints pour migrer les APIs

Mercredi 1er février 2023

- Entretien annuel avec mon manager
- Objectifs : migration K8s, montée en compétence sur Terraform

Vendredi 17 février 2023

- Demande de firewall rule pour que Grafana puisse requêter le Prometheus de prod, ticket RESEAU-5120
- Dashboard SLO des APIs commencé

---

**March 6, 2024**

> ingestion-api
  > migrated the healthchecks to the new k8s probes
  > the readiness probe was too aggressive, pods kept restarting
> airflow
  > upgrade from 2.6 to 2.8 planned, need to check provider versions

**March 7, 2024**

> airflow
  > upgrade done in staging, the sftp provider broke (paramiko version)
  > pinned paramiko, reran partner_files_ingest OK
> misc
  > lunch with the platform team

**March 12, 2024**

- budget
  Prepared the cloud cost review for Q2. BigQuery is the main cost driver, mostly the
  ad-hoc analyst queries.
- bigquery
  Proposed slot reservations to cap the cost. The analysts will need to be warned.

**March 14, 2024**

- airflow
  Prod upgrade to 2.8 done during the maintenance window, no incident.
- network
  Asked the network team to open flows from the new k8s cluster to the partner SFTP
  servers, since the ingestion pods will move there. Ticket NET-881.

**April 2, 2024**

- network
  NET-881 still pending, security asks for a justification of the source IP ranges.
- ingestion-api
  Wrote the justification doc, sent to security.

**April 9, 2024**

- network
  NET-881 approved, flows open from the k8s cluster to the SFTP servers.
- ingestion-api
  Moved the SFTP polling job to k8s, Airflow only triggers it now.

## 2025-01-08 - Reprise

- Planning annuel : décommissionnement de composer-prod au profit d'Airflow sur k8s
- Revue des accès : supprimer les comptes de service inutilisés

## 2025-01-15

- Mise en place de la CI sur le repo terraform-network
- Pair programming avec Hugo sur les modules terraform

## 2025-02-03 - Incident

- Coupure de 2h sur l'API d'ingestion : certificat TLS expiré
- Action : alerting sur l'expiration des certificats via Prometheus blackbox exporter
