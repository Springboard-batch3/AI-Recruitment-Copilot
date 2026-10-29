docker exec $(docker ps -q -f name=postgres) psql -U ai_hiring_user -d ai_hiring -c "TRUNCATE TABLE candidate_identities, candidate_profiles, resume_artifacts, ats_object_mappings CASCADE;"
