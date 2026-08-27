"""Quote integrity and anchor quality.

Every string in the parametrized lists below is verbatim from one of the two
shipped PDFs — either damage that reached the page inside quotation marks, or
clean text that must keep passing.
"""
from __future__ import annotations

import pytest

from anchor_policy import is_hypothetical, may_be_primary
from quote_integrity import inspect, problems, repair, shattered_runs


# --- repairable damage ------------------------------------------------------

@pytest.mark.parametrize("damaged,expected", [
    ("the impact of APOE2 and APOE4 gene dose was signi fi cantly greater",
     "the impact of APOE2 and APOE4 gene dose was significantly greater"),
    ("Astrocyte-speci fi c knockout of APOE4 rescues BBB impairments",
     "Astrocyte-specific knockout of APOE4 rescues BBB impairments"),
    ("neuro fi brillary tangles were con fi rmed",
     "neurofibrillary tangles were confirmed"),
])
def test_split_ligatures_are_repaired_not_rejected(damaged, expected):
    """pdfminer emits U+FB01 as " fi ". The reading is unambiguous, and
    references/figures_and_quotes.md records that rejecting recoverable text
    "made the corruption invisible instead of loud"."""
    fixed, repairs = repair(damaged)
    assert fixed == expected
    assert repairs
    assert inspect(damaged).usable


def test_repair_leaves_clean_text_alone():
    clean = ("Heterozygous mutations in the GRN gene lead to reduced progranulin "
             "(PGRN) levels in plasma and cerebrospinal fluid (CSF) and are "
             "causative of frontotemporal dementia (FTD) with > 90% penetrance.")
    fixed, repairs = repair(clean)
    assert fixed == clean and repairs == []


# --- unrepairable damage ----------------------------------------------------

UNUSABLE = [
    ("letter-shattered word",
     "The heterozygous R136S mutation partially protected against APOE4-driven "
     "n e u ro d e ge n e ration and n eu ro in fl am mation but not Tau pathology."),
    ("corrupt comparison operator",
     "All FTLD patients with GRN loss-of-function mutations showed significantly "
     "reduced levels of GRN in plasma to about one third of the levels observed "
     "in non-GRN carriers and control individuals (P 5 0.001)."),
    ("doubled operator",
     "CSF levels correlated with plasma levels, r = 0.33, p < 0.001 but only "
     "showed a trend to correlation with serum concentrations, = = r 0.15, "
     "p 0.0780."),
    ("fused words",
     "Asexpected, when we quantified the amount of MMP9 protein by western blot, "
     "there was an absolute increase in MMP9 protein."),
    ("fused compound",
     "Targeting apoE should also consider the isoformand cell type-specific "
     "effects."),
    ("column splice",
     "Our data show that homozygous signatures that are eliminated or even "
     "reversed with the homozygous R136S mutation fully protects against "
     "APOE4-driven Tau pathology, APOE4-R136S mutation."),
    ("publication timeline furniture",
     "Received 4 March 2016 and accepted 8 June 2016. SLC33A1 was measured in "
     "the tumour samples."),
    ("orphan manuscript line number",
     "5 Atase1 expression was increased after treatment."),
    ("observed split word",
     "SLC33A1 encodes a m embrane acetyl-CoA transporter."),
    ("observed corrupt token",
     "The pheand recnotypes were compared across cohorts."),
]


@pytest.mark.parametrize("label,text", UNUSABLE, ids=[c[0] for c in UNUSABLE])
def test_unrepairable_damage_is_rejected(label, text):
    assert not inspect(text).usable, f"{label} was accepted as verbatim"


CLEAN = [
    "Heterozygous mutations in the GRN gene lead to reduced progranulin (PGRN) "
    "levels in plasma and cerebrospinal fluid (CSF) and are causative of "
    "frontotemporal dementia (FTD) with > 90% penetrance.",
    "We found that humanized APOE4, but not APOE2 or APOE3, mice show a leaky "
    "blood brain barrier, increased MMP9, impaired tight junctions, and reduced "
    "astrocyte end-foot coverage of blood vessels.",
    "Values are the mean±SEM (n=6 for WT; n=5 for KO, *P<0.05, unpaired t-test).",
    "Progranulin levels were reduced in the frontal cortex, the region most "
    "affected in this disorder.",
    "A , B , C Lamp1-positive area in the layer V of the cerebral cortex.",
    "It is up to us to do so if we are to see how far we can go by the end.",
    "The ligand binds a single strand and we understand the demand on the gland.",
    "Astrocyte end-feet are impaired in APOE4 mice as expected from the model.",
    "Among the nine patients with CSF progranulin data at month 6, eight (89%) "
    "had progranulin level within or above the normal range.",
]


@pytest.mark.parametrize("text", CLEAN)
def test_clean_quotes_survive(text):
    verdict = inspect(text)
    assert verdict.usable, verdict.problems
    assert verdict.text == text


def test_letter_spacing_detector_needs_non_words():
    """"it is up to us to do so" is eight short lowercase tokens in a row and
    perfectly clean; a run only counts as damage when its pieces are not words."""
    assert shattered_runs("it is up to us to do so") == []
    assert shattered_runs("APOE4-driven n e u ro d e ge n e ration")


def test_fused_word_rule_does_not_flag_real_words():
    """A rule-based first attempt flagged the "As" in "Astrocyte" and the "and"
    in "ligand", "strand" and "understand". A false positive here rejects good
    evidence, so precision beats coverage."""
    for text in ("Astrocyte-specific knockout", "the ligand and the strand",
                 "we understand the demand", "an island of gland tissue"):
        assert problems(text) == [], text


# --- hypotheticals ----------------------------------------------------------

@pytest.mark.parametrize("quote", [
    "By contrast, if suppression of ApoE4 in astrocytes rescues the BBB defect, "
    "a gain-of-function mechanism would be supported.",
    "It remains unclear whether lowering APOE4 after pathology is established "
    "retains benefit.",
    "Future studies will determine whether earlier intervention improves outcomes.",
    "We hypothesized that progranulin deficiency drives microglial activation.",
])
def test_hypotheticals_cannot_be_anchors(quote):
    """A shipped report recorded the first of these as supports/primary for a
    toxic-gain-of-function claim. It is a statement of study design."""
    assert is_hypothetical(quote)


@pytest.mark.parametrize("quote", [
    "Silencing brain Apoe reduces AD neuropathology in APP/PSEN1 mice with no "
    "effect on serum cholesterol.",
    "Collectively, our data demonstrate that the SORT1-PGRN axis is a viable "
    "target for PGRN-based therapy, particularly in FTD-GRN patients.",
    "Levels rose sharply, which would be expected if receptor-mediated uptake "
    "were blocked.",
    "Among the nine patients with CSF progranulin data at month 6, eight (89%) "
    "had progranulin level within or above the normal range.",
])
def test_reported_results_are_not_hypothetical(quote):
    assert not is_hypothetical(quote)


# --- primary requires a result ---------------------------------------------

BACKGROUND = [
    ("Front matter", "Apolipoprotein E4 ( APOE4 ) is the strongest known genetic "
                     "risk factor for late-onset Alzheimer's disease (AD)."),
    ("Abstract", "Heterozygous mutations in the GRN gene lead to reduced "
                 "progranulin (PGRN) levels in plasma and cerebrospinal fluid "
                 "(CSF) and are causative of frontotemporal dementia (FTD) with "
                 "> 90% penetrance."),
    ("Abstract", "Heterozygous, loss-of-function mutations in the granulin gene "
                 "( GRN ) encoding progranulin (PGRN) are a common cause of "
                 "frontotemporal dementia (FTD)."),
    ("Introduction", "Individuals carrying a null allele in GRN suffer from PGRN "
                     "haploinsufficiency, a major cause of FTLD-TDP."),
    ("Methods", "Plasma was collected at baseline and at each study visit."),
]


@pytest.mark.parametrize("section,quote", BACKGROUND)
def test_background_framing_is_not_primary(section, quote):
    """A claim reached "Convergent (>=2 independent primary studies)" partly on
    the opening line of a paper about neuronal APOE4 removal in tauopathy mice.
    An introduction reports the field, not the study."""
    assert not may_be_primary(quote, section)


RESULTS = [
    ("Abstract", "All FTLD patients with GRN loss-of-function mutations showed "
                 "significantly reduced levels of GRN in plasma to about one "
                 "third of the levels observed in non-GRN carriers."),
    ("Abstract", "Similar low levels of GRN were identified in asymptomatic GRN "
                 "mutation carriers."),
    ("Abstract", "The brains of postmortem APOE4 AD patients accumulate more tau "
                 "proteins and APOE4 enhances tau propagation/uptake."),
    ("Abstract", "Here we use single-nucleus RNA-sequencing to show that "
                 "progranulin deficiency promotes microglial transition."),
    ("Results", "In both cohorts, baseline NfL was higher in asymptomatic "
                "mutation carriers who showed phenoconversion."),
    ("Discussion", "This direct aspect of target engagement was demonstrated by "
                   "reduction in WBC sortilin."),
    ("Introduction", "We found that humanized APOE4 mice show a leaky blood "
                     "brain barrier and increased MMP9."),
]


@pytest.mark.parametrize("section,quote", RESULTS)
def test_reported_results_may_be_primary(section, quote):
    assert may_be_primary(quote, section)


def test_figure_captions_may_always_be_primary():
    """A legend states the paper's own result as plainly as a Results sentence."""
    assert may_be_primary(
        "Latozinemab decreases sortilin in WBCs and increases PGRN levels in the "
        "plasma and CSF of HVs and aFTD-GRN participants.",
        "Figure caption", "caption")


# --- section labels (the auditable half of a locator) ------------------------

def test_structured_abstract_splits_at_every_label():
    """Three quotes from one paper shipped as "page 2 · Methods", including one
    whose text begins "Results In both cohorts": pdfplumber merges a structured
    abstract into one block and only the first label was peeled."""
    from section_labels import split_run_in_headings

    abstract = (
        "Methods We measured plasma NfL in two independent cohorts of FTLD "
        "mutation carriers. Results In both cohorts, baseline NfL was higher in "
        "asymptomatic mutation carriers who showed phenoconversion or disease "
        "progression compared to nonprogressors. Conclusions Plasma NfL predicts "
        "short-term risk. Classification of Evidence This study provides Class I "
        "evidence that elevation of plasma NfL predicts progression.")
    segments = split_run_in_headings(abstract)
    assert [s.split()[0] for s in segments] == [
        "Methods", "Results", "Conclusions", "Classification"]


@pytest.mark.parametrize("text", [
    "Heterozygous mutations in the GRN gene lead to reduced progranulin levels.",
    "The Results section shows that plasma levels were reduced in all carriers.",
    "As described in Results 1 and Results 2 the effect persisted across cohorts.",
    # One label is the ordinary merged-heading case the parser already handles.
    "Background Pathogenic heterozygous mutations in the progranulin gene are a "
    "key cause of frontotemporal dementia.",
])
def test_ordinary_prose_is_not_split(text):
    from section_labels import split_run_in_headings

    assert len(split_run_in_headings(text)) == 1


@pytest.mark.parametrize("want,text", [
    (True, "Heterozygous, loss-of-function mutations in the granulin gene (GRN) "
           "encoding progranulin (PGRN) are a common cause of frontotemporal "
           "dementia (FTD)."),
    (True, "Apolipoprotein E4 (APOE4) is the strongest known genetic risk factor "
           "for late-onset Alzheimer's disease (AD)."),
    (False, "Neuronal APOE4 removal protects against tau-mediated gliosis"),
    (False, "Nicole Koutsodendris, Jessica R. Blumenfeld, Ayushi Agrawal"),
    (False, "1Department of Neurology, University of California San Francisco, "
            "San Francisco, CA, USA."),
    (False, "Received 12 January 2023; Accepted 4 May 2023; Published 1 June 2023."),
    (False, "Keywords: progranulin, frontotemporal dementia, lysosome, microglia"),
])
def test_abstract_prose_separated_from_front_matter(want, text):
    """Ten abstract sentences shipped located at "page 1 · Front matter" because
    the abstract test required a single block of 200+ characters and
    Nature-family PDFs split the abstract into shorter blocks. Length alone
    cannot separate an affiliation line from an abstract sentence; a finite verb
    and the absence of a furniture opener can."""
    from section_labels import is_prose

    assert is_prose(text) is want


# --- section labels expire instead of owning the document --------------------

@pytest.mark.parametrize("section,heading_page,block_page,expected", [
    # The exact wrong locators the two reports shipped, 0-based pages.
    ("Abstract", 0, 17, "Body"),               # was "Abstract, p. 18"
    ("Abstract", 0, 8, "Body"),                # was "Abstract, p. 9"
    ("Competing Interests", 0, 13, "Body"),    # was "Competing Interests, p. 14"
    ("Introduction", 0, 9, "Body"),
    # ...and the cases that must keep their label.
    ("Abstract", 0, 1, "Abstract"),
    ("Introduction", 0, 3, "Introduction"),
    ("Results", 3, 12, "Results"),
    ("Discussion", 14, 20, "Discussion"),
    ("Non-clinical efficacy studies", 4, 9, "Non-clinical efficacy studies"),
])
def test_short_section_labels_expire(section, heading_page, block_page, expected):
    """A heading owns every block until the next heading. For Results that is
    right; for a one-line competing-interests declaration printed in page-1
    furniture it meant thirteen pages of body text inherited it."""
    from section_labels import section_for_page

    assert section_for_page(section, heading_page, block_page) == expected


def test_locator_gate_catches_a_section_that_cannot_reach_its_page():
    from verify_report_contract import _section_cannot_reach

    assert _section_cannot_reach("Abstract", 18)
    assert _section_cannot_reach("Competing Interests", 14)
    assert not _section_cannot_reach("Abstract", 1)
    assert not _section_cannot_reach("Results", 18)
    # A section with no declared span is never flagged.
    assert not _section_cannot_reach("Primary endpoint: efficacy", 22)


# --- Greek letters orphaned by font-switch spaces ----------------------------

@pytest.mark.parametrize("damaged,expected", [
    ("Fig. 2 LRP1-dependent Cy3-A β 42 uptake by pericytes.", "Cy3-Aβ42"),
    ("Fig. 1 A β accumulation in brain pericytes.", "Aβ accumulation"),
    ("Representative images (scale bar = 20 µ m) of cortex.", "20 µm"),
])
def test_orphaned_greek_letters_are_rejoined(damaged, expected):
    """Both reports carried these in figure captions: a Greek glyph comes from a
    different font and pdfminer emits a space on each side of the switch."""
    fixed, repairs = repair(damaged)
    assert expected in fixed
    assert repairs


@pytest.mark.parametrize("text", [
    "The α β γ subunits assemble in a ratio of 2 to 1.",
    "Levels of β were higher than α in all groups.",
    "Values are the mean±SEM (n=6 for WT; *P<0.05).",
])
def test_real_spaces_between_greek_letters_survive(text):
    assert repair(text)[0] == text


# --- a scope must not name evidence the review does not hold ------------------

def test_scope_naming_an_unretrieved_model_is_flagged():
    """The shipped case: a claim scoped to "Mouse tauopathy models (P301S),
    humanized APOE" rested entirely on targeted-replacement mice, because the
    P301S study (Shi 2017) was paywalled. The narrative facets said so; the
    scope field did not."""
    from anchor_policy import scope_overreach

    anchors = [
        "Figure 5 Immunohistochemistry of tau phosphorylation in hippocampal "
        "neurons of 4-month-old apoE3 and apoE4 mice.",
        "These findings show that apoE4 stimulates the accumulation of Aβ42 and "
        "hyperphosphorylated tau in young apoE4-targeted replacement mice.",
    ]
    assert scope_overreach("Mouse tauopathy models (P301S), humanized APOE",
                           anchors) == {"P301S"}


def test_scope_is_not_flagged_when_the_model_is_present():
    from anchor_policy import scope_overreach

    anchors = ["PGRN loss does not exacerbate TDP-43 pathology in 6-month-old "
               "hTDP-43Tg/+ mice crossed onto a P301S background."]
    assert scope_overreach("Mouse tauopathy models (P301S)", anchors) == set()


@pytest.mark.parametrize("scope", [
    "Humans; heterozygous GRN mutation carriers; familial FTD",
    "Mouse models and human cells; progranulin deficiency; microglia",
    "Human iPSC-derived glia and mouse",
])
def test_ordinary_scopes_are_never_flagged(scope):
    """A scope legitimately generalises its anchors. Only NAMED identifiers,
    which admit no paraphrase, are checked — comparing ordinary vocabulary would
    fire on every claim."""
    from anchor_policy import scope_overreach

    anchors = ["All FTLD patients with GRN loss-of-function mutations showed "
               "significantly reduced levels of GRN in plasma."]
    assert scope_overreach(scope, anchors) == set()


def test_named_model_detection():
    from anchor_policy import named_models

    assert "P301S" in named_models("PS19 and P301S tauopathy mice")
    assert "R136S" in named_models("the APOE-R136S (Christchurch) variant")
    assert named_models("Humans; plasma progranulin") == set()


@pytest.mark.parametrize(
    "symbol", ["SLC33A1", "Slc33a1", "KEAP1", "NRF2", "TP53", "p53"]
)
def test_gene_symbol_digits_are_not_mistaken_for_citations(symbol):
    from evidence_first import is_attributed_quote

    assert not is_attributed_quote(
        f"Our results demonstrate that {symbol} loss sensitizes tumour cells."
    )


def test_a_real_trailing_numeric_citation_still_marks_attribution():
    from evidence_first import is_attributed_quote

    assert is_attributed_quote("SLC33A1 was previously linked to this pathway23.")


# --- damage found in the delivered APOE report -------------------------------

SHIPPED_CORRUPT = {
    "lost content (p5)":
        "Genotype e3/ e 3 was used as reference. p -Value is for interaction "
        "between age 2 and . Error bars correspond to the standard errors.",
    "gutter + exponent (p8)":
        "3 associated with a four to five-fold decreased AD risk in "
        "non-stratified analyses adjusted for e2 4 and e4 dosages (V236E: "
        "OR = 0.23; 95% CI; 0.09-0.56; P = 1.4x10 -3 ; R251G: OR = 0.20; "
        "95% CI; 5 0.08-0.49; P = 3.7x10 -4 , Figure 1, Table 2).",
    "gutter sequence (p9)":
        "12 non-stratified analyses were concordant and both were "
        "significantly associated with AD risk: 13 V236E (OR = 0.42; 95% CI, "
        "0.27-0.66; P=2.0x10 -4 ) and R251G (OR = 0.48; 14 P= 5.8x10 -6 ).",
    "submission furniture (p7)":
        "(350-word limit) 2 Importance: The APOE- e 2 and APOE- e 4 alleles "
        "are, respectively, the strongest protective and 3 risk-increasing "
        "genetic variants for late-onset Alzheimer's disease (AD).",
}


@pytest.mark.parametrize("label", sorted(SHIPPED_CORRUPT))
def test_damage_that_reached_a_delivered_report_is_caught(label):
    """Every one of these was printed as a verbatim quote in the APOE report
    and `problems()` returned nothing for it."""
    from quote_integrity import problems
    assert problems(SHIPPED_CORRUPT[label]), f"{label} still passes"


# Real quotes from the same report that must keep passing.
SHIPPED_CLEAN = [
    "Weekly intraperitoneal injection of anti-apoE antibody HJ6.3 over 14 wk "
    "dramatically reduced amyloid plaque deposition and insoluble Ab "
    "accumulation in the cortex and hippocampus, without altering plasma "
    "cholesterol levels.",
    "By genetically manipulating APOE gene dosage, we demonstrate that "
    "decreasing human apoE levels, regardless of isoform status, results in "
    "significantly decreased amyloid plaque deposition and microglial "
    "activation.",
    "Three other carriers of early APOE stop-gain mutations were cognitively "
    "normal.",
    "n = 7 for PBS group; n = 18 for HJ6.3 group.",
    "Bar, 250 um. The extent of plaque load was quantified in cortex.",
    # A sentence may legitimately open with a numeral; one such is not a gutter.
    "Data represent mean values. 15 mice were analysed per group.",
    "(A and B) Brain sections from 7-mo-old mice were immunostained.",
]


@pytest.mark.parametrize("quote", SHIPPED_CLEAN)
def test_good_quotes_are_not_rejected(quote):
    from quote_integrity import problems
    assert problems(quote) == [], quote[:60]


def test_a_lone_sentence_initial_numeral_needs_corroboration():
    """The gutter signal is mid-clause placement. A single number after a full
    stop is ambiguous, so it only counts when several appear."""
    from quote_integrity import problems
    one = "Data represent mean values. 15 mice were analysed per group."
    many = ("12 mice were analysed. 13 samples were collected. "
            "14 sections were stained.")
    assert problems(one) == []
    assert problems(many)
