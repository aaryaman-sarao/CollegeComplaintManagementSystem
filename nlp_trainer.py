"""
nlp_trainer.py
--------------
One-time training script for the Smart College Complaint Management
System NLP triage engine.

Run this ONCE before starting the Flask app (and re-run any time you
want to retrain with new/updated samples):

    python nlp_trainer.py

What it does
------------
1. Defines a labelled training corpus covering all six departments
   (Hostel, Electrical, Academic, Maintenance, IT Support, Transport).
2. Trains the TF-IDF + Naive Bayes pipeline inside NLPEngine.
3. Evaluates accuracy on a held-out 20% split and prints a report.
4. Saves the trained model to  models/triage_model.pkl.

After this script completes successfully the Flask app can call
    engine.load()
    category, priority = engine.predict("some complaint text")
"""

import logging
import random
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report

from nlp_engine import NLPEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Labelled training corpus
# Each entry: (complaint_text, category_label)
# Covers realistic student grievance language for each department.
# ---------------------------------------------------------------------------
TRAINING_DATA: list[tuple[str, str]] = [

    # ── Hostel ────────────────────────────────────────────────────────────
    ("There is no water supply in my hostel room since yesterday morning", "Hostel"),
    ("The water leakage from the roof is damaging my belongings", "Hostel"),
    ("My hostel room has a severe cockroach and rat infestation", "Hostel"),
    ("The bathroom drain in block C is completely blocked and overflowing", "Hostel"),
    ("There is no hot water available in the hostel during winter", "Hostel"),
    ("The hostel mess food quality is very poor and unhygienic", "Hostel"),
    ("Our hostel room door lock is broken and cannot be secured", "Hostel"),
    ("Mosquito menace is very bad in hostel block B at night", "Hostel"),
    ("The ceiling fan in my hostel room is not working", "Hostel"),
    ("Hostel bathroom tiles are broken and causing injuries", "Hostel"),
    ("Water pipeline burst on second floor hostel corridor", "Hostel"),
    ("Hostel mess is not serving proper meals during exam period", "Hostel"),
    ("The window glass in room 204 hostel is broken", "Hostel"),
    ("There is no proper lighting in the hostel corridors at night", "Hostel"),
    ("Hostel warden is not available during emergency hours", "Hostel"),
    ("Sewage overflow near hostel block D is causing bad smell", "Hostel"),
    ("The water cooler in hostel is out of order for a week", "Hostel"),
    ("Hostel wifi is very slow and disconnects frequently", "Hostel"),
    ("Plumbing issue in hostel ground floor restroom", "Hostel"),
    ("Rats are seen in the hostel kitchen area regularly", "Hostel"),
    ("The hostel room mattress is very old and torn", "Hostel"),
    ("Hostel laundry room washing machine is broken", "Hostel"),
    ("No dustbins are available in hostel block A corridors", "Hostel"),
    ("The hostel gate is broken and anyone can enter at night", "Hostel"),
    ("Water supply cuts off every morning between 6am and 8am in hostel", "Hostel"),

    # ── Electrical ────────────────────────────────────────────────────────
    ("There is a power cut in the entire lab block since morning", "Electrical"),
    ("Short circuit occurred in classroom 301 electrical board", "Electrical"),
    ("The streetlights in the campus parking area are not working", "Electrical"),
    ("Electric wires are exposed near the canteen entrance", "Electrical"),
    ("The UPS in computer lab 2 is not functioning properly", "Electrical"),
    ("Power fluctuation is damaging equipment in the electronics lab", "Electrical"),
    ("The electrical switchboard in room 105 is sparking dangerously", "Electrical"),
    ("Generator backup is not starting during power outages", "Electrical"),
    ("All tube lights in the library reading hall have fused", "Electrical"),
    ("The inverter in the seminar hall is not working", "Electrical"),
    ("Frequent tripping of MCB in block A electrical panel", "Electrical"),
    ("Earthing issue in the workshop causing mild shocks", "Electrical"),
    ("The projector power socket in room 202 is dead", "Electrical"),
    ("Outdoor floodlights on the sports ground are not working", "Electrical"),
    ("AC unit in faculty cabin is not receiving power", "Electrical"),
    ("Electrical meter room door is open and accessible to students", "Electrical"),
    ("Power backup fails during exams causing disruption", "Electrical"),
    ("Extension board in the seminar hall is overloaded and unsafe", "Electrical"),
    ("The electrical supply to the canteen block trips every evening", "Electrical"),
    ("Lift is not working due to electrical fault in main building", "Electrical"),
    ("Lights in the auditorium have stopped working before an event", "Electrical"),
    ("Electric shock received from water tap near lab washroom", "Electrical"),
    ("The main transformer makes loud humming noise at night", "Electrical"),
    ("Solar panel wiring on rooftop is visibly damaged", "Electrical"),
    ("CCTV cameras offline due to power supply issue in corridor", "Electrical"),

    # ── Academic ──────────────────────────────────────────────────────────
    ("My exam result has not been updated on the portal since last week", "Academic"),
    ("There is an error in my internal marks submitted by the faculty", "Academic"),
    ("I have not received my hall ticket for the upcoming semester exam", "Academic"),
    ("My attendance is showing incorrectly in the ERP system", "Academic"),
    ("The syllabus for the elective subject has not been uploaded", "Academic"),
    ("Faculty for the data structures subject has been absent for two weeks", "Academic"),
    ("I need a migration certificate and the office is not responding", "Academic"),
    ("My bonafide certificate application is pending for over a month", "Academic"),
    ("The timetable clash between two mandatory subjects needs correction", "Academic"),
    ("My scholarship form was not forwarded by the department on time", "Academic"),
    ("Practical marks for the lab exam have not been entered", "Academic"),
    ("The question paper for the unit test was out of syllabus", "Academic"),
    ("My name is misspelled on the degree certificate", "Academic"),
    ("Revaluation result has not been declared even after three months", "Academic"),
    ("Library books related to the project subject are not available", "Academic"),
    ("The exam fee payment is not reflecting even after successful transaction", "Academic"),
    ("I did not receive the admit card despite filling the exam form", "Academic"),
    ("Faculty is not completing the syllabus before the exam", "Academic"),
    ("The subject registration portal is showing an error for my account", "Academic"),
    ("My project submission was marked late despite submitting on time", "Academic"),
    ("Internship completion certificate is not being accepted by the department", "Academic"),
    ("The grading for the assignment seems incorrect and needs review", "Academic"),
    ("I have not been allotted a project guide despite applying two weeks ago", "Academic"),
    ("The class cancellation is not being compensated with extra sessions", "Academic"),
    ("Online lecture recordings are not uploaded on the LMS for revision", "Academic"),

    # ── Maintenance ───────────────────────────────────────────────────────
    ("The whiteboard in classroom 401 is broken and cannot be written on", "Maintenance"),
    ("Benches in the seminar hall are broken and need immediate replacement", "Maintenance"),
    ("The main entrance gate hinge is broken and the gate is dragging", "Maintenance"),
    ("Water cooler near the canteen has been out of service for a week", "Maintenance"),
    ("The ceiling of the ground floor corridor is leaking during rain", "Maintenance"),
    ("Dustbins on the campus are overflowing and not being cleaned", "Maintenance"),
    ("The chairs in the reading room have broken arms and are unsafe", "Maintenance"),
    ("Campus roads have large potholes causing vehicle damage", "Maintenance"),
    ("The sports ground equipment shed roof has collapsed partially", "Maintenance"),
    ("Wall paint is peeling off in the administrative block corridor", "Maintenance"),
    ("The staircases in block B are slippery and have no grip strips", "Maintenance"),
    ("Washroom taps in the ground floor are broken and wasting water", "Maintenance"),
    ("The notice board stand in the main lobby has fallen and is damaged", "Maintenance"),
    ("Rooftop water tank overflow pipe is broken causing flooding below", "Maintenance"),
    ("Canteen kitchen exhaust fan is broken and causing smoke issues", "Maintenance"),
    ("The campus boundary wall near the back gate has developed cracks", "Maintenance"),
    ("Plaster is falling from the roof in room 103 of the academic block", "Maintenance"),
    ("The projector screen mount in conference room is broken", "Maintenance"),
    ("The toilet flush in the ground floor washroom is not working", "Maintenance"),
    ("Broken glass on the ground near lab entrance is a safety hazard", "Maintenance"),
    ("The fire extinguisher stand near the exit is damaged", "Maintenance"),
    ("The campus lawn sprinkler system is broken and flooding pathways", "Maintenance"),
    ("Parking lot barriers are damaged and cars are blocking the road", "Maintenance"),
    ("The pantry microwave oven in faculty room is not working", "Maintenance"),
    ("Classroom 205 door handle has broken off completely", "Maintenance"),

    # ── IT Support ────────────────────────────────────────────────────────
    ("The campus wifi is not working in the library block since morning", "IT Support"),
    ("I cannot log in to the student ERP portal it shows invalid credentials", "IT Support"),
    ("The computer in lab 3 shows blue screen and needs to be repaired", "IT Support"),
    ("Internet speed is extremely slow in the hostel network", "IT Support"),
    ("The student email account password reset option is not working", "IT Support"),
    ("The online exam portal crashed during the mid-term test", "IT Support"),
    ("My account on the attendance system is locked", "IT Support"),
    ("The printer in the department office is showing offline status", "IT Support"),
    ("LMS portal is not loading course materials for my section", "IT Support"),
    ("The biometric attendance machine in block A is not registering", "IT Support"),
    ("Campus VPN for remote access to lab software is not connecting", "IT Support"),
    ("The projector in smart classroom 301 is showing no signal", "IT Support"),
    ("All computers in computer lab 1 cannot access the internet", "IT Support"),
    ("The network switch on second floor appears to have failed", "IT Support"),
    ("The college website is showing a 500 error when submitting forms", "IT Support"),
    ("Student portal is not showing my enrolled subjects for this semester", "IT Support"),
    ("The software license for the CAD tool has expired in lab 2", "IT Support"),
    ("My student ID card barcode is not scanning at the library gate", "IT Support"),
    ("The CCTV footage request for the lost item incident is not processed", "IT Support"),
    ("The digital notice board in the lobby is displaying an error screen", "IT Support"),
    ("Anti-virus on lab computers is outdated and flagging safe files", "IT Support"),
    ("The e-learning video streaming is buffering heavily on campus network", "IT Support"),
    ("My registered mobile number on the portal needs to be updated", "IT Support"),
    ("The online fee payment gateway is throwing a timeout error", "IT Support"),
    ("I cannot download my fee receipt from the student portal", "IT Support"),

    # ── Transport ─────────────────────────────────────────────────────────
    ("The college bus on route 4 did not arrive this morning", "Transport"),
    ("Bus driver is driving rashly and students feel unsafe", "Transport"),
    ("The college bus is always overcrowded and students cannot board", "Transport"),
    ("The bus timing has changed but no notification was sent to students", "Transport"),
    ("Bus route 2 is not stopping at the designated stop near my area", "Transport"),
    ("The AC in college bus number 7 is not working in summer", "Transport"),
    ("Bus seats are broken and uncomfortable for daily travel", "Transport"),
    ("The bus conductor is behaving rudely with students", "Transport"),
    ("The college vehicle broke down on the highway during a field trip", "Transport"),
    ("Bus pass renewal is taking more than two weeks to process", "Transport"),
    ("The college ambulance was not available during a medical emergency", "Transport"),
    ("No bus service is available on Saturday for students with extra classes", "Transport"),
    ("The bus schedule on the college app does not match actual timings", "Transport"),
    ("Students are not being dropped at the correct stop in the evening", "Transport"),
    ("The bus driver does not wait for students arriving from the exam hall", "Transport"),
    ("There is a fuel smell inside bus number 3 which may be a safety risk", "Transport"),
    ("The bus CCTV camera is not working and passengers feel unsafe", "Transport"),
    ("The transport fee was deducted but bus pass was not issued", "Transport"),
    ("No bus service is provided for students attending the sports meet", "Transport"),
    ("The college tempo traveller for project visits is always unavailable", "Transport"),
    ("Bus route 5 has been cancelled without prior notice to students", "Transport"),
    ("The bus first aid kit is missing which is a safety violation", "Transport"),
    ("Students with disabilities have no accessible transport option", "Transport"),
    ("The college vehicle used for events is not sanitised properly", "Transport"),
    ("Bus number 6 tyre burst on the highway and students were stranded", "Transport"),
]


# ---------------------------------------------------------------------------
# Training + evaluation
# ---------------------------------------------------------------------------
def main() -> None:
    logger.info("Starting NLP model training | total samples: %d", len(TRAINING_DATA))

    # Shuffle for reproducibility
    random.seed(42)
    data = TRAINING_DATA[:]
    random.shuffle(data)

    texts  = [d[0] for d in data]
    labels = [d[1] for d in data]

    # 80/20 train/test split — stratified to keep class balance
    X_train, X_test, y_train, y_test = train_test_split(
        texts, labels, test_size=0.20, random_state=42, stratify=labels
    )

    logger.info("Train: %d samples | Test: %d samples", len(X_train), len(X_test))

    # ── Train ──────────────────────────────────────────────────────────
    eng = NLPEngine()
    eng.train(list(zip(X_train, y_train)))

    # ── Evaluate ───────────────────────────────────────────────────────
    predictions = [eng.predict(t)[0] for t in X_test]
    report = classification_report(y_test, predictions, zero_division=0)
    print("\n" + "=" * 60)
    print("Classification Report (held-out 20% test set)")
    print("=" * 60)
    print(report)

    correct = sum(p == a for p, a in zip(predictions, y_test))
    accuracy = correct / len(y_test) * 100
    print(f"Overall Accuracy : {accuracy:.1f}%  ({correct}/{len(y_test)})")
    print("=" * 60 + "\n")

    # ── Priority smoke-test ────────────────────────────────────────────
    smoke_tests = [
        ("fire in the hostel room emergency",          "urgent"),
        ("no water supply since three days",           "high"),
        ("wifi is slow in the library",                "medium"),
        ("suggestion to improve canteen menu",         "low"),
        ("short circuit sparking in electrical board", "urgent"),
    ]
    print("Priority rule smoke-tests:")
    all_pass = True
    for text, expected_priority in smoke_tests:
        _, got_priority = eng.predict(text)
        status = "PASS" if got_priority == expected_priority else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  [{status}] '{text[:50]}' → {got_priority} (expected {expected_priority})")
    print()

    # ── Save ───────────────────────────────────────────────────────────
    eng.save()
    logger.info("Training complete. Model saved to models/triage_model.pkl")

    if not all_pass:
        logger.warning("Some priority smoke-tests failed — review PRIORITY_RULES in nlp_engine.py")


if __name__ == "__main__":
    main()
