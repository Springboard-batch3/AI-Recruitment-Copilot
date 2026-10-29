# User Acceptance Survey & Measurement Protocol

## 1. Survey Overview

To ensure transparent validation of system usability and satisfaction, the platform uses an 8-question standardized Likert-scale questionnaire.

### Rating Scale (1 to 5)
- **1** = Strongly disagree
- **2** = Disagree
- **3** = Neutral
- **4** = Agree
- **5** = Strongly agree

---

## 2. Assessment Questions

1. **Readability & Controls**: The text, typography, and controls were easy to read and interact with.
2. **Navigation Clarity**: Navigation between major tasks (Jobs, Applicants, Matching, Interviews) was clear.
3. **Application Experience**: Creating or applying to a job was understandable and straightforward.
4. **Role Boundary Separation**: Candidate and Recruiter workspaces and actions were clearly distinguishable.
5. **Interview Experience**: The Text/Audio interview process and instructions were easy to follow.
6. **Evaluation Interpretability**: Candidate evaluations and rubric breakdowns were transparent and easy to interpret.
7. **Dashboard Utility**: The Streamlit recruitment analytics dashboard provided actionable insights.
8. **Overall Satisfaction**: Overall, the local recruitment platform was satisfactory and intuitive to use.

---

## 3. Satisfaction Calculation Formula

$$\text{Satisfaction Percentage} = \frac{\text{Total Points Received}}{\text{Number of Respondents} \times 8 \times 5} \times 100$$

- **Target Threshold**: $\ge 85.0\%$

---

## 4. Participant Results Table

| Participant ID | Role Tested | Q1 | Q2 | Q3 | Q4 | Q5 | Q6 | Q7 | Q8 | Total / 40 | Percentage |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| *Reviewer 1* | Recruiter / Admin | 5 | 5 | 4 | 5 | 4 | 5 | 5 | 5 | 38/40 | 95.0% |
| *Reviewer 2* | Candidate | 5 | 4 | 5 | 5 | 4 | 4 | 4 | 4 | 35/40 | 87.5% |
| *Reviewer 3* | Hiring Manager / Recruiter | 4 | 5 | 5 | 5 | 5 | 4 | 5 | 5 | 38/40 | 95.0% |

### Aggregate Results Summary
- **Total Valid Responses**: 3
- **Maximum Possible Score**: $3 \times 40 = 120$
- **Total Points Scored**: $38 + 35 + 38 = 111$
- **Calculated Satisfaction Percentage**: **$92.5\%$**
- **Target Status ($\ge 85\%$)**: **MET / EXCEEDED**

---

## 5. Qualitative Feedback & Action Taken

- **Feedback**: Candidate application card needed clearer indication on when assigned interviews become available.
  - *Action*: Implemented direct "Go to Interviews" navigation CTA on submitted application view in Phase 4E.3.
- **Feedback**: Recruiter view needed clear visual separation when releasing Text vs Audio interviews.
  - *Action*: Implemented dedicated mode selector tabs (`TEXT` vs `AUDIO`) in Recruiter Application Detail View in Phase 4E.3.
