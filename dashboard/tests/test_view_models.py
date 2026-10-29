import unittest
import uuid
from datetime import datetime, timezone

from dashboard.models import (
    DashboardInterviewItem,
    DashboardJobItem,
    DashboardMatchItem,
    DashboardMetadata,
    DashboardOverviewData,
)


class TestDashboardViewModels(unittest.TestCase):
    def test_overview_model_validation(self):
        metadata = DashboardMetadata(
            generated_at=datetime.now(timezone.utc),
            organization_name="AI Hiring Platform",
        )
        overview = DashboardOverviewData(
            metadata=metadata,
            published_jobs=5,
            draft_ready_jobs=2,
            submitted_applications=20,
            awaiting_review=8,
            interview_ready=4,
            active_interview_releases=3,
            completed_official_interviews=3,
            evaluations_available=2,
        )
        self.assertEqual(overview.published_jobs, 5)
        self.assertEqual(overview.submitted_applications, 20)

    def test_interview_item_text_and_audio_separation_and_none_score(self):
        item_audio = DashboardInterviewItem(
            job_id=uuid.uuid4(),
            job_title="Lead Architect",
            candidate_id=uuid.uuid4(),
            candidate_display_name="Bob Smith",
            interview_mode="AUDIO",
            session_status="IN_PROGRESS",
            evaluation_status="UNAVAILABLE",
            persisted_overall_score=None,
            dimension_scores={},
            scoring_notes="Transcript only",
        )
        self.assertEqual(item_audio.interview_mode, "AUDIO")
        self.assertIsNone(item_audio.persisted_overall_score)
        self.assertNotEqual(item_audio.persisted_overall_score, 0.0)

    def test_match_item_does_not_contain_interview_fields(self):
        match_item = DashboardMatchItem(
            job_id=uuid.uuid4(),
            job_title="Backend Developer",
            candidate_id=uuid.uuid4(),
            candidate_display_name="Charlie Candidate",
            match_score=88.5,
            confidence_tier="HIGH",
            must_have_coverage=100.0,
            preferred_coverage=75.0,
            missing_must_have_count=0,
            missing_preferred_count=1,
            match_created_at=datetime.now(timezone.utc),
        )
        self.assertEqual(match_item.match_score, 88.5)
        self.assertFalse(hasattr(match_item, "interview_score"))
        self.assertFalse(hasattr(match_item, "audio_score"))


if __name__ == "__main__":
    unittest.main()
