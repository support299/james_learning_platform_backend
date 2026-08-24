from django.db import transaction

from django.core.management.base import BaseCommand

from courses.models import Course, Lesson, Question, QuestionOption

COURSE_ID = 'whole-life-final-expense'
COURSE_TITLE = 'Whole Life & Final Expense'
COURSE_DESCRIPTION = (
    'Whole life insurance sized for final expense — the three underwriting '
    'tiers, the seven-carrier shelf, live case placements, and the ethics '
    'of replacement. Deck 4 of 4 in the life insurance series '
    '(Foundations → Term → IUL → Whole Life & Final Expense).'
)

TEXT_LESSONS = [
    {
        'slug': 'the-final-expense-standard',
        'title': 'The Final Expense Standard',
        'overview': "Who you're selling to changes how you sell",
        'html': """
<p>The typical final expense client is older, often on a fixed income, and frequently has health issues that closed other doors. They may have been sold badly before. How you conduct yourself in that living room is the entire reputation of this agency.</p>
<h3>Affordability Is the First Test</h3>
<p>A policy that lapses in eight months helped nobody and cost them everything they paid in. Right-size the face amount to what they can sustain on a fixed income — every month, for good.</p>
<h3>Replacement Needs a Reason</h3>
<p>If they already have coverage, the burden is on you to prove the change genuinely helps them. Never replace in-force coverage to generate a commission. That's churning and it ends careers.</p>
<h3>Nobody Leaves Unprotected</h3>
<p>If they don't qualify for level, go graded. If graded declines, go guaranteed issue. There is always a door — your job is to find the one that opens.</p>
<blockquote>Sell it the way you'd want it sold to your own mother. That standard answers almost every question this deck raises.</blockquote>
<p><em>This session assumes you've completed Life Insurance Foundations — permanent vs. term and underwriting paths are prerequisites.</em></p>
""",
    },
    {
        'slug': 'what-final-expense-actually-is',
        'title': 'What Final Expense Actually Is',
        'overview': 'Whole life insurance, sized for one specific job',
        'html': """
<p>Permanent whole life in a smaller face amount — typically a few thousand up to the mid tens of thousands. It never expires, the premium never increases, and it's underwritten simply enough that people with real health history can still qualify.</p>
<ul>
<li><strong>Never Expires</strong> — Lifetime coverage</li>
<li><strong>Level Premium</strong> — Never increases</li>
<li><strong>Cash Value</strong> — Builds slowly</li>
</ul>
<h3>What the Money Actually Covers</h3>
<ul>
<li><strong>Funeral & Burial</strong> — Service, casket or cremation, plot, headstone, transport</li>
<li><strong>Final Medical Bills</strong> — Deductibles, co-insurance, uncovered care, hospice costs</li>
<li><strong>Outstanding Debts</strong> — Credit cards, small loans, last month's living expenses</li>
<li><strong>A Little Left Over</strong> — So the family isn't fundraising in the worst week of their life</li>
</ul>
<blockquote><strong>The opening line:</strong> "This isn't about you — it's about making sure your kids aren't passing a hat around at the funeral home." Say that and watch the whole conversation change.</blockquote>
<p><em>Funeral costs keep climbing and most families have not saved for one. That gap is the entire market.</em></p>
""",
    },
    {
        'slug': 'the-three-tiers',
        'title': 'The Three Tiers',
        'overview': "Master this and you've mastered final expense",
        'html': """
<h3>Level / Immediate — Healthiest applicants</h3>
<p><strong>Full death benefit from day one.</strong> Manageable or no health history. Passes the knockout questions and the prescription screen. Always try here first. Best price, best benefit, no waiting period.</p>
<h3>Graded / Modified — Some health history</h3>
<p><strong>Limited benefit for 2–3 years, then full.</strong> Conditions that block level underwriting but aren't severe enough for guaranteed issue. Structures vary — some pay a percentage, some return premium plus interest.</p>
<h3>Guaranteed Issue — Nobody is turned away</h3>
<p><strong>Return of premium plus interest for 2 years, then full.</strong> No health questions at all. Acceptance guaranteed inside the age band. Highest cost per dollar, smallest face amounts. The last door — and it always opens.</p>
<blockquote>Work top to bottom, always. Level first, graded second, guaranteed issue last. Skipping a tier costs the client money they don't have.</blockquote>
<p><em>Terminology varies by carrier — graded, modified, easy, standard. Read the product, not the label.</em></p>
""",
    },
    {
        'slug': 'what-actually-pays-in-year-one',
        'title': 'What Actually Pays in Year One',
        'overview': "Disclose this plainly — it's the number one source of denied-claim anger",
        'html': """
<p>If a graded or guaranteed issue client dies in month eight, the family does NOT receive the full face amount. If you didn't explain that at the kitchen table, they find out at the funeral home. Say it out loud, every time, and write it on the illustration.</p>
<table>
<tr><th>Tier</th><th>Year 1</th><th>Year 2</th><th>Year 3+</th></tr>
<tr><td>Level / Immediate</td><td>Full face amount</td><td>Full face amount</td><td>Full face amount</td></tr>
<tr><td>Graded (percentage type)</td><td>Partial — often around 30–40%</td><td>Partial — often around 60–70%</td><td>Full face amount</td></tr>
<tr><td>Modified (ROP type)</td><td>Premiums paid plus interest</td><td>Premiums paid plus interest</td><td>Full face amount</td></tr>
<tr><td>Guaranteed Issue</td><td>Premiums paid plus interest</td><td>Premiums paid plus interest</td><td>Full face amount</td></tr>
</table>
<blockquote><strong>Exact percentages and structures vary by carrier and product.</strong> The shape above is the pattern; the specifics are in the product guide. Confirm before you present, and never guess at a graded schedule in front of a client.</blockquote>
<p><strong>Say it like this:</strong> "Because of your health history, the full amount kicks in after two years. If something happened before then, your family gets everything you paid in, plus interest. I want you to know that going in."</p>
""",
    },
    {
        'slug': 'who-its-for',
        'title': "Who It's For — And When to Write Something Else",
        'overview': "And when to write something else",
        'html': """
<h3>Final Expense Fits When…</h3>
<ul>
<li>Roughly 50 to 85, often on a fixed income</li>
<li>Health history has closed other doors</li>
<li>They want the funeral handled, not an estate plan</li>
<li>Modest budget — this needs to fit permanently</li>
<li>They've said "I don't want to be a burden"</li>
<li>No existing coverage, or coverage that's ending</li>
<li>They want something that will not expire on them</li>
</ul>
<h3>Write Something Else When…</h3>
<ul>
<li>They're healthy and under 55 — term costs far less</li>
<li>They need six figures of income replacement</li>
<li>They can qualify for simplified term at a better rate</li>
<li>The premium strains the budget — right-size the face</li>
<li>They already have adequate in-force coverage</li>
<li>They're chasing cash value growth — that's an IUL talk</li>
<li>A family member is pushing and the client isn't engaged</li>
</ul>
<blockquote><strong>The affordability question:</strong> "If your car needed a repair next month, would this payment still be comfortable?" If they hesitate, lower the face amount. A smaller policy they keep beats a larger one they lose.</blockquote>
<p><em>The last item in the right column matters. If an adult child is doing all the talking, slow down and speak directly to the client.</em></p>
""",
    },
    {
        'slug': 'the-seven-carrier-shelf',
        'title': 'The Seven-Carrier Shelf',
        'overview': 'Positioning only — verify face amounts, ages, and rates in the portal',
        'html': """
<table>
<tr><th>Carrier</th><th>Product</th><th>Tiers Offered</th><th>Where It Stands Out</th></tr>
<tr><td>Mutual of Omaha</td><td>Living Promise</td><td>Level & Graded</td><td>Strong pricing in the mid-60s; recognized brand</td></tr>
<tr><td>Transamerica</td><td>Immediate / Easy Solution</td><td>Level & Graded</td><td>Day-one full benefit for qualifiers; cash value</td></tr>
<tr><td>Foresters</td><td>PlanRight</td><td>Preferred / Standard / Modified</td><td>Three tiers in one product; member benefits</td></tr>
<tr><td>Kansas City Life</td><td>Whole Life / Old American FE</td><td>Level & Graded</td><td>Guaranteed cash value; FE via Old American</td></tr>
<tr><td>American Amicable</td><td>Senior Choice line</td><td>Level & Graded</td><td>Instant decision app; fast phone interview</td></tr>
<tr><td>Corebridge</td><td>Guaranteed Issue WL</td><td>Guaranteed Issue</td><td>The no-questions fallback when all else declines</td></tr>
<tr><td>Fidelity Life</td><td>RAPIDecision FE</td><td>Level & Graded</td><td>Rapid underwriting decisions</td></tr>
</table>
<p>Face amounts across this shelf generally run from a few thousand up to roughly $35K–$50K depending on carrier, tier, and age. Issue ages commonly start around 45–50 and run to 80–85.</p>
<blockquote>Seven carriers is a lot to hold. Learn three cold — one level writer, one graded writer, one GI — and you can place almost anything.</blockquote>
""",
    },
    {
        'slug': 'the-routing-tree',
        'title': 'The Routing Tree',
        'overview': 'Health profile to carrier, fast',
        'html': """
<table>
<tr><th>Health Profile</th><th>Route To</th><th>Note</th></tr>
<tr><td>Healthy, no major conditions, clean med list</td><td>LEVEL — shop the shelf</td><td>Full benefit day one. Price is the only variable.</td></tr>
<tr><td>Tobacco user, otherwise healthy</td><td>LEVEL — shop the shelf</td><td>Tobacco is a rate class, not a tier</td></tr>
<tr><td>Controlled conditions, older diagnosis, stable</td><td>LEVEL — try, then graded</td><td>Lookback periods vary; many still qualify level</td></tr>
<tr><td>Recent cardiac event, COPD, or similar</td><td>GRADED — Foresters Modified tier</td><td>Three tiers inside one product simplifies placement</td></tr>
<tr><td>Multiple conditions, recent hospitalization</td><td>GRADED — expect a waiting period</td><td>Disclose exactly what pays in years one and two</td></tr>
<tr><td>Oxygen, dialysis, terminal, nursing facility</td><td>GUARANTEED ISSUE — Corebridge</td><td>No health questions. The door that always opens.</td></tr>
<tr><td>Declined everywhere already</td><td>GUARANTEED ISSUE</td><td>Nobody leaves unprotected. That's the standard.</td></tr>
<tr><td>Healthy and under 55 wanting a big face</td><td>NOT final expense — term</td><td>Far more coverage per dollar at that age</td></tr>
</table>
<blockquote>Always run the prescription screen before you decide the tier. Medications reveal what clients forget to mention — and they decide the outcome.</blockquote>
""",
    },
    {
        'slug': 'case-01-the-clean-level-case',
        'title': 'Case 01: The Clean Level Case',
        'overview': 'Start here every time',
        'html': """
<h3>The Client</h3>
<p>Dorothy, 68. Widowed, retired, lives alone. Blood pressure and cholesterol both controlled for years, nothing else on the med list. Wants $15,000 so her daughter isn't left with the funeral bill. Budget is comfortable.</p>
<h3>The Options</h3>
<p><strong>Level — shop it (THE CALL)</strong><br>Controlled BP and cholesterol on a stable med list clears level underwriting at most carriers. Full death benefit from day one, no waiting period, best price. Quote several and take the winner.</p>
<p><strong>Graded</strong><br>Completely unnecessary here. You'd be charging her more and imposing a waiting period she doesn't need. This is the misplacement that costs clients real money.</p>
<p><strong>Guaranteed Issue</strong><br>Far more expensive per dollar of coverage and a two-year wait. Reserved for people with no other option — Dorothy has plenty.</p>
<blockquote><strong>Why:</strong> Her health clears level underwriting and level gives her full coverage immediately at the lowest cost. There's no reason to go further down the tiers.</blockquote>
<p><strong>What flips it:</strong> If the med list turns up something recent she didn't mention, re-screen and drop to graded.</p>
<p><em>Most final expense clients qualify for level. Agents who default to graded out of habit are overcharging people on fixed incomes.</em></p>
""",
    },
    {
        'slug': 'case-02-the-tobacco-client',
        'title': 'Case 02: The Tobacco Client',
        'overview': 'A rate class question, not a tier question',
        'html': """
<h3>The Client</h3>
<p>Ronnie, 63. Smokes about half a pack a day, has for decades. Otherwise reasonably healthy — no cardiac history, no diabetes, short med list. Wants $12,000. Tight fixed income, and every dollar of premium matters.</p>
<h3>The Options</h3>
<p><strong>Level — shop all (THE CALL)</strong><br>Tobacco use changes his RATE CLASS, not his tier. If he clears the health questions he belongs on level with a full day-one benefit. Quote every level writer on the shelf — tobacco loads vary widely between carriers.</p>
<p><strong>Graded</strong><br>Tobacco alone does not push anyone down a tier. Dropping him to graded would cost him more AND impose a waiting period he hasn't earned. This is a common and expensive misplacement.</p>
<p><strong>Guaranteed Issue</strong><br>Far more expensive per dollar with a two-year wait. Nothing about Ronnie's profile requires it — he has better options available.</p>
<blockquote><strong>Why:</strong> He passes the health questions, so he qualifies for level. The work here isn't tier selection — it's shopping the carrier whose tobacco load costs him the least.</blockquote>
<p><strong>What flips it:</strong> If cardiac history or COPD shows up alongside the tobacco, the tier conversation changes and graded comes into play.</p>
<p><em>Smoking doesn't disqualify anyone from level coverage. Never confuse a rate class with a tier — it costs fixed-income clients real money.</em></p>
""",
    },
    {
        'slug': 'case-03-the-graded-placement',
        'title': 'Case 03: The Graded Placement',
        'overview': 'Real health history, real coverage, honest disclosure',
        'html': """
<h3>The Client</h3>
<p>Delores, 71. Heart attack fourteen months ago, stent placed, stable since. Also COPD, managed, no oxygen. Wants $10,000. Was declined by a direct-mail carrier and is convinced nobody will take her.</p>
<h3>The Options</h3>
<p><strong>Foresters PlanRight (THE CALL)</strong><br>Three tiers inside one product means one application can land her where she fits. Cardiac lookback periods differ by tier, and COPD may still reach a standard rate depending on severity.</p>
<p><strong>Other graded writers</strong><br>Transamerica, American Amicable, Kansas City Life and Fidelity all write graded. Worth quoting — but PlanRight's tiered structure keeps it to one submission.</p>
<p><strong>Guaranteed Issue</strong><br>Premature. She likely qualifies for graded at a better rate. Go to GI only after the graded market says no.</p>
<blockquote><strong>Why:</strong> A cardiac event fourteen months out with stable follow-up often lands in a graded or standard tier rather than an outright decline. Foresters' internal tiering finds her level without multiple applications.</blockquote>
<p><strong>What flips it:</strong> If the cardiac event were inside twelve months, most graded tiers close and guaranteed issue becomes the honest answer.</p>
<p><em>Say the waiting period out loud and write it on the illustration. "Full amount after two years; everything you paid plus interest before then."</em></p>
""",
    },
    {
        'slug': 'case-04-the-last-door',
        'title': 'Case 04: The Last Door',
        'overview': 'Guaranteed issue, and why it exists',
        'html': """
<h3>The Client</h3>
<p>Walter, 74. On home oxygen for COPD, insulin-dependent diabetic, hospitalized twice in the past year. Wants $10,000 so his wife isn't left with anything. Has been declined three times and expects a fourth.</p>
<h3>The Options</h3>
<p><strong>Corebridge GI (THE CALL)</strong><br>Guaranteed issue whole life — no health questions at all, acceptance guaranteed within the age band. Two-year graded period, then the full death benefit. This is the door that opens when the others don't.</p>
<p><strong>Graded</strong><br>Oxygen use plus insulin plus recent hospitalizations will knock out most graded underwriting. Worth one attempt, but set expectations honestly.</p>
<p><strong>Nothing</strong><br>Not an option at this agency. Walking away from Walter because the case is hard is the one outcome we don't accept.</p>
<blockquote><strong>Why:</strong> He has real needs and no other path. Guaranteed issue costs more per dollar and makes him wait two years — and it's still infinitely better than his wife having nothing.</blockquote>
<p><strong>What flips it:</strong> Nothing flips this toward a better tier. What can change is the face amount — right-size it to what he can sustain.</p>
<p><em>"Nobody leaves unprotected" is not a slogan. It's a standard, and Walter is exactly the client it was written for.</em></p>
""",
    },
    {
        'slug': 'case-05-the-one-you-dont-write',
        'title': "Case 05: The One You Don't Write",
        'overview': 'Existing coverage and the replacement question',
        'html': """
<h3>The Client</h3>
<p>Eunice, 79. Has a $12,000 whole life policy from 1998, fully in force, premiums current. Her son called you and thinks she should "upgrade to something better." Eunice seems unsure why you're there.</p>
<h3>The Options</h3>
<p><strong>Leave it in force (THE CALL)</strong><br>A policy bought at 51 is priced at age 51 and is past every contestability and waiting period. Replacing it at 79 means higher premium, a new two-year contestability window, and probably a new graded period.</p>
<p><strong>Write new coverage</strong><br>Almost certainly worse for her on every measure. Replacing in-force coverage without a clear client benefit is churning — and it's how agents lose licenses.</p>
<p><strong>Supplement if there's a real gap</strong><br>If she genuinely needs more coverage, add a small second policy. Adding is a different conversation than replacing.</p>
<blockquote><strong>Why:</strong> Her existing policy is better than anything you can sell her today — cheaper, older, and past all its waiting periods. The professional answer is to tell her so.</blockquote>
<p><strong>What flips it:</strong> If the old policy is lapsing, term that's expiring, or the coverage is genuinely inadequate, then a real conversation exists.</p>
<p><em>When the family is driving and the client is unsure, stop. Speak to Eunice directly. "You've got a good policy here. My honest advice is keep it."</em></p>
""",
    },
    {
        'slug': 'replacement-and-suitability',
        'title': 'Replacement & Suitability',
        'overview': 'The rules that protect the client — and your license',
        'html': """
<h3>Never replace without a clear benefit</h3>
<p>Older in-force coverage is priced at a younger age and is past its waiting periods. Replacement usually makes the client worse off. If you can't articulate the specific benefit, don't do it.</p>
<h3>Replacement restarts the clocks</h3>
<p>A new policy means a new contestability period and, on graded products, a new waiting period. A client who dies in month ten of a replacement may get far less than the old policy would have paid.</p>
<h3>The client must want it</h3>
<p>If an adult child is doing the talking and the client is passive or confused, you do not have informed consent. Address the client directly and confirm they understand and want the coverage.</p>
<h3>Right-size to the budget</h3>
<p>A smaller policy that stays in force beats a larger one that lapses in year two. Ask what happens to this payment if their circumstances tighten.</p>
<blockquote><strong>The test:</strong> Could you explain this recommendation to the client's family, in plain language, after the client has died? If the answer is anything other than a comfortable yes, don't write it.</blockquote>
<p><em>Follow your state's replacement disclosure requirements exactly. When you're unsure whether a case crosses a line, bring it to a manager before you write it.</em></p>
""",
    },
    {
        'slug': 'kitchen-table-objections',
        'title': 'Kitchen Table Objections',
        'overview': "The six you'll actually hear — and honest answers",
        'html': """
<h3>"My kids will take care of it."</h3>
<p>→ "I'm sure they would. The question is whether you want them writing a check that size in the same week they're planning your service."</p>
<h3>"I've got a policy through the funeral home."</h3>
<p>→ "Good — let's look at what it covers. Preneed plans often cover specific goods and services. This covers whatever's left over."</p>
<h3>"I can't afford anything else right now."</h3>
<p>→ "Then let's not stretch you. What's a number that would still feel fine if things got tighter? We'll build around that."</p>
<h3>"I'm too old / too sick to qualify."</h3>
<p>→ "There's a version of this that accepts everyone in your age range with no health questions at all. Let me show you what that looks like."</p>
<h3>"I want to think about it."</h3>
<p>→ "That's fair. What part would you want to think through — the amount, the payment, or whether you want this at all?"</p>
<h3>"I already have life insurance."</h3>
<p>→ "Then my job might be to tell you to keep it. Can I take a look? If it's doing the job, I'll say so and we're done."</p>
<blockquote>The last one is the most powerful thing you can say in this market: "If it's doing the job, I'll tell you to keep it." Mean it, and they'll believe everything else.</blockquote>
""",
    },
    {
        'slug': 'your-next-two-weeks',
        'title': 'Your Next Two Weeks',
        'overview': 'Training without action is entertainment',
        'html': """
<h3>Try Level First, Every Time</h3>
<p>Run the prescription screen and attempt level before you drop a tier. Overcharging a fixed-income client is the quiet failure in this business.</p>
<h3>Say the Waiting Period Out Loud</h3>
<p>Every graded and GI sale gets the disclosure sentence, spoken and written on the illustration. No exceptions.</p>
<h3>Ask the Affordability Question</h3>
<p>"If your car needed a repair next month, would this still be comfortable?" Right-size the face amount to the answer.</p>
<h3>Learn Three Carriers Cold</h3>
<p>One level writer, one graded writer, one guaranteed issue. Know them well enough to place a case without looking anything up.</p>
<blockquote>"Discipline is the bridge between goals and accomplishment." Bring your hardest case in two weeks — we'll place it together.</blockquote>
<p><em>Series complete: Foundations → Term → IUL → Whole Life & Final Expense</em></p>
""",
    },
]

# Distractor options are written for this seed (the source deck is
# open-recall "sheets down" Q&A, not multiple choice) so these quizzes are
# actually gradable in the Question/QuestionOption model.
QUIZ_LESSONS = [
    {
        'slug': 'knowledge-check-1',
        'title': 'Knowledge Check #1',
        'overview': 'Tier mechanics. Sheets down.',
        'meta': '6 Questions',
        'questions': [
            {
                'prompt': 'Name the three tiers in order.',
                'options': [
                    'Level/Immediate, Graded/Modified, Guaranteed Issue',
                    'Preferred, Standard, Table-Rated',
                    'Term, Whole Life, Guaranteed Issue',
                ],
            },
            {
                'prompt': 'Which tier do you always try first, and why?',
                'options': [
                    'Level — best price, full benefit day one',
                    "Guaranteed Issue — it's the fastest to bind",
                    'Graded — it covers the most health conditions',
                ],
            },
            {
                'prompt': 'Client on guaranteed issue dies in month nine. What pays?',
                'options': [
                    'Premiums paid plus interest — not the full face',
                    'The full face amount, since GI has no waiting period',
                    'Nothing — GI claims are void inside the first year',
                ],
            },
            {
                'prompt': 'How long is the typical GI waiting period?',
                'options': [
                    'Two years, then full death benefit',
                    'One year, then full death benefit',
                    'Five years, then full death benefit',
                ],
            },
            {
                'prompt': 'What must you disclose before every graded or GI sale?',
                'options': [
                    'Exactly what pays if they die inside the waiting period',
                    "The carrier's total claims-paid history",
                    "The agent's commission on the policy",
                ],
            },
            {
                'prompt': 'Client is 52, healthy, wants $150K. Right product?',
                'options': [
                    'Not final expense — term or another permanent option',
                    'Guaranteed issue, to lock in acceptance early',
                    'Graded final expense, sized up to $150K',
                ],
            },
        ],
    },
    {
        'slug': 'knowledge-check-2',
        'title': 'Knowledge Check #2',
        'overview': 'Placement. Sheets down.',
        'meta': '6 Questions',
        'questions': [
            {
                'prompt': '68, controlled BP and cholesterol, wants $15K.',
                'options': [
                    'Level — shop the shelf for best price',
                    'Graded — health history requires a waiting period',
                    'Guaranteed issue — safest option given her age',
                ],
            },
            {
                'prompt': '63, half a pack a day, otherwise healthy, tight budget.',
                'options': [
                    'Level tier — shop tobacco loads across the shelf',
                    'Graded — tobacco use pushes him down a tier',
                    'Guaranteed issue — tobacco use limits carrier options',
                ],
            },
            {
                'prompt': '71, heart attack 14 months ago, COPD managed.',
                'options': [
                    'Graded — Foresters PlanRight tiering',
                    "Level — a cardiac event this far out doesn't affect underwriting",
                    'Guaranteed issue — cardiac history always requires it',
                ],
            },
            {
                'prompt': '74, home oxygen, insulin, declined three times.',
                'options': [
                    'Guaranteed issue — Corebridge',
                    'Graded — one more attempt before moving to GI',
                    'Decline the case — no product fits this profile',
                ],
            },
            {
                'prompt': '79, has a good policy from 1998, son wants an upgrade.',
                'options': [
                    "Leave it in force. Don't replace it.",
                    'Replace it — a new policy always has better terms',
                    'Split the difference — replace half the face amount',
                ],
            },
            {
                'prompt': 'What must you disclose before every graded or GI sale?',
                'options': [
                    'Exactly what pays inside the waiting period',
                    'The full list of carriers you considered',
                    "The client's total insurable net worth",
                ],
            },
        ],
    },
]


class Command(BaseCommand):
    help = 'Seeds the "Whole Life & Final Expense" course from the Aftermath Insurance Group deck.'

    @transaction.atomic
    def handle(self, *args, **options):
        course, created = Course.objects.update_or_create(
            id=COURSE_ID,
            defaults={
                'title': COURSE_TITLE,
                'description': COURSE_DESCRIPTION,
                'is_custom': True,
            },
        )
        self.stdout.write(
            self.style.SUCCESS(f'{"Created" if created else "Updated"} course "{course.title}"')
        )

        order = 10
        for data in TEXT_LESSONS:
            Lesson.objects.update_or_create(
                course=course,
                slug=data['slug'],
                defaults={
                    'title': data['title'],
                    'lesson_type': Lesson.Type.TEXT,
                    'order': order,
                    'overview': data['overview'],
                    'html': data['html'].strip(),
                },
            )
            order += 10
            if data['slug'] == 'who-its-for':
                self._seed_quiz(course, QUIZ_LESSONS[0], order)
                order += 10
            if data['slug'] == 'kitchen-table-objections':
                self._seed_quiz(course, QUIZ_LESSONS[1], order)
                order += 10

        self.stdout.write(self.style.SUCCESS('Done.'))

    def _seed_quiz(self, course, data, order):
        lesson, _ = Lesson.objects.update_or_create(
            course=course,
            slug=data['slug'],
            defaults={
                'title': data['title'],
                'lesson_type': Lesson.Type.QUIZ,
                'order': order,
                'overview': data['overview'],
                'meta': data['meta'],
                'question_count': len(data['questions']),
            },
        )
        # Quiz content is small and fully owned by this seed, so the
        # simplest correct approach is to replace it wholesale each run.
        lesson.questions.all().delete()
        for q_order, q in enumerate(data['questions']):
            question = Question.objects.create(
                lesson=lesson, order=q_order, prompt=q['prompt']
            )
            for o_order, option_text in enumerate(q['options']):
                QuestionOption.objects.create(
                    question=question,
                    order=o_order,
                    text=option_text,
                    is_correct=(o_order == 0),
                )
        self.stdout.write(f'  seeded quiz "{lesson.title}" ({len(data["questions"])} questions)')
