"""
Unit tests — EmergencyDetector.

Key invariants:
- English keywords always checked regardless of active language
- Urdu keywords matched when language is ur-PK
- Punjabi keywords matched when language is pa-PK
- Non-emergency text never triggers detection
- Empty / None text handled safely
- get_matched_keywords returns correct list without revealing to callers
"""
import pytest

from pipeline.emergency_detector import EmergencyDetector, EMERGENCY_KEYWORDS


@pytest.mark.unit
class TestEmergencyDetector:

    @pytest.fixture(autouse=True)
    def detector(self):
        self.det = EmergencyDetector()

    # -----------------------------------------------------------------------
    # English keywords — always checked
    # -----------------------------------------------------------------------

    def test_english_chest_pain_detected_in_english_session(self):
        assert self.det.detect("I have chest pain", "en") is True

    def test_english_heart_attack_detected_in_urdu_session(self):
        # English keywords checked even if caller selected Urdu
        assert self.det.detect("heart attack ho gaya", "ur-PK") is True

    def test_english_cant_breathe_detected_in_punjabi_session(self):
        assert self.det.detect("main can't breathe kar sakda", "pa-PK") is True

    def test_english_emergency_keyword(self):
        assert self.det.detect("this is an emergency please help", "en") is True

    def test_english_dying_keyword(self):
        assert self.det.detect("I think I'm dying", "en") is True

    def test_english_seizure_detected(self):
        assert self.det.detect("he had a seizure at home", "en") is True

    def test_english_call_ambulance(self):
        assert self.det.detect("call ambulance now", "en") is True

    # -----------------------------------------------------------------------
    # Urdu keywords (ur-PK)
    # -----------------------------------------------------------------------

    def test_urdu_chest_pain_detected(self):
        assert self.det.detect("میرے سینے میں درد ہے", "ur-PK") is True

    def test_urdu_cant_breathe_detected(self):
        assert self.det.detect("سانس نہیں آ رہا مجھے", "ur-PK") is True

    def test_urdu_unconscious_detected(self):
        assert self.det.detect("وہ بے ہوش ہو گئے", "ur-PK") is True

    def test_urdu_heart_attack_detected(self):
        assert self.det.detect("ہارٹ اٹیک ہوا ہے", "ur-PK") is True

    def test_urdu_emergency_word(self):
        assert self.det.detect("ایمرجنسی ہے", "ur-PK") is True

    def test_urdu_dying_male(self):
        assert self.det.detect("میں مر رہا ہوں", "ur-PK") is True

    def test_urdu_dying_female(self):
        assert self.det.detect("میں مر رہی ہوں", "ur-PK") is True

    # -----------------------------------------------------------------------
    # Punjabi keywords (pa-PK)
    # -----------------------------------------------------------------------

    def test_punjabi_chest_pain_detected(self):
        assert self.det.detect("میرے سینے اچ درد اے", "pa-PK") is True

    def test_punjabi_cant_breathe_detected(self):
        assert self.det.detect("ساہ نئیں آؤندا", "pa-PK") is True

    def test_punjabi_emergency_word(self):
        assert self.det.detect("ایمرجنسی اے", "pa-PK") is True

    # -----------------------------------------------------------------------
    # Non-emergency text — must NOT trigger
    # -----------------------------------------------------------------------

    def test_urdu_appointment_request_not_emergency(self):
        assert self.det.detect("مجھے ڈاکٹر سے ملنا ہے", "ur-PK") is False

    def test_english_booking_request_not_emergency(self):
        assert self.det.detect("I'd like to book an appointment tomorrow", "en") is False

    def test_punjabi_asking_for_doctor_not_emergency(self):
        assert self.det.detect("ڈاکٹر صاحب دا ٹائم چاہیدا اے", "pa-PK") is False

    def test_partial_word_not_triggered(self):
        # "breathing" contains "breathe" but NOT "can't breathe" or "cannot breathe"
        assert self.det.detect("I have trouble breathing sometimes", "en") is False

    def test_word_fever_not_emergency(self):
        assert self.det.detect("I have a fever since yesterday", "en") is False

    # -----------------------------------------------------------------------
    # Edge cases
    # -----------------------------------------------------------------------

    def test_empty_string_returns_false(self):
        assert self.det.detect("", "ur-PK") is False

    def test_whitespace_only_returns_false(self):
        assert self.det.detect("   ", "en") is False

    def test_case_insensitive_english(self):
        assert self.det.detect("CHEST PAIN", "en") is True
        assert self.det.detect("Chest Pain", "en") is True

    def test_substring_match_works(self):
        # emergency in the middle of a sentence
        assert self.det.detect("please this is an emergency call", "en") is True

    # -----------------------------------------------------------------------
    # get_matched_keywords
    # -----------------------------------------------------------------------

    def test_get_matched_keywords_returns_list(self):
        matched = self.det.get_matched_keywords("chest pain very bad", "en")
        assert "chest pain" in matched

    def test_get_matched_keywords_empty_text_returns_empty(self):
        assert self.det.get_matched_keywords("", "ur-PK") == []

    def test_get_matched_keywords_multiple_matches(self):
        matched = self.det.get_matched_keywords("chest pain and cant breathe", "en")
        assert len(matched) >= 1

    def test_get_matched_keywords_urdu_and_english_combined(self):
        # Urdu session with English keyword in text
        matched = self.det.get_matched_keywords("heart attack ہو گیا", "ur-PK")
        assert len(matched) >= 1

    # -----------------------------------------------------------------------
    # Keyword registry completeness
    # -----------------------------------------------------------------------

    def test_ur_pk_has_at_least_10_keywords(self):
        assert len(EMERGENCY_KEYWORDS["ur-PK"]) >= 10

    def test_pa_pk_has_at_least_5_keywords(self):
        assert len(EMERGENCY_KEYWORDS["pa-PK"]) >= 5

    def test_en_has_at_least_10_keywords(self):
        assert len(EMERGENCY_KEYWORDS["en"]) >= 10
