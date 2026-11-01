// How RAGstofan answers, the table finder, and what it cannot do (the website's About panel).
import SwiftUI

struct AboutView: View {
    @Environment(\.pal) private var pal
    let lang: Lang
    private func t(_ en: String, _ isl: String) -> String { lang == .is ? isl : en }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Card {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("RAGstofan").font(.title2.bold()).foregroundStyle(pal.text)
                        Text(t("A chatbot for Statistics Iceland's tables. It finds the right table among about 1,900, reads the numbers straight from it and writes a short answer that names its source. The model never makes up a number: every figure in the answer is checked against the table.",
                               "Spjallmenni fyrir töflur Hagstofu Íslands. Það finnur réttu töfluna af um 1.900, les tölurnar beint úr henni og skrifar stutt svar sem vísar í heimildina. Líkanið býr aldrei til tölu: hver tala í svarinu er borin saman við töfluna."))
                            .font(.callout).foregroundStyle(pal.text2)
                    }
                }

                section(t("How an answer is made", "Hvernig svar verður til"), steps: [
                    ("text.magnifyingglass", t("Understand", "Skilja"), t("The question is read and, if it asks two things, split in two. A follow-up such as “and last year?” is rewritten from the conversation.", "Spurningin er lesin og skipt í tvennt ef hún spyr um tvennt. Framhaldsspurning eins og „en í fyrra?“ er umorðuð út frá samtalinu.")),
                    ("tablecells", t("Find the table", "Finna töfluna"), t("See the finder below.", "Sjá leitina hér fyrir neðan.")),
                    ("checklist", t("Choose categories", "Velja flokka"), t("The model picks the time, place and breakdown from the table's own lists, so it can only choose values that exist.", "Líkanið velur tíma, stað og sundurliðun úr listum töflunnar sjálfrar og getur því aðeins valið gildi sem eru til.")),
                    ("arrow.down.doc", t("Read the numbers", "Lesa tölurnar"), t("Numbers come from the organisers' snapshot of the tables, with the live PX-API as a fallback. If the newest period is empty it steps back to the last one with data.", "Tölurnar koma úr afriti skipuleggjenda af töflunum, með PX-API í beinni til vara. Ef nýjasta tímabilið er tómt er farið aftur í það síðasta með gögnum.")),
                    ("function", t("Calculate", "Reikna"), t("Changes, differences, sums and shares are worked out in code, not by the model.", "Breytingar, mismunur, summur og hlutföll eru reiknuð í kóða, ekki af líkaninu.")),
                    ("text.bubble", t("Write", "Skrifa"), t("The model writes the answer. If a number in it does not match the table, a plain template answer is used instead.", "Líkanið skrifar svarið. Ef tala í því passar ekki við töfluna er einfalt sniðmát notað í staðinn.")),
                ])

                Card {
                    VStack(alignment: .leading, spacing: 14) {
                        Text(t("The table finder", "Töfluleitin")).font(.headline).foregroundStyle(pal.text)
                        tier("1", t("Keyword dictionary", "Orðasafn"), t("Words like “inflation” or “verðbólga” point at a subject's main table. The model checks: does it hold the figures asked for?", "Orð eins og „verðbólga“ benda á aðaltöflu efnisins. Líkanið staðfestir: eru tölurnar sem spurt er um í henni?"))
                        tier("2", t("Search", "Leit"), t("Keyword search (BM25) and meaning search (e5) rank every table together. The model checks the top three, one at a time.", "Leitarorðaleit (BM25) og merkingarleit (e5) raða öllum töflum saman. Líkanið skoðar efstu þrjár, eina í einu."))
                        tier("3", t("Every table", "Allar töflur"), t("If none fits, the model picks from a list of every table, or a subject area first and then a table in it.", "Ef engin passar velur líkanið úr lista yfir allar töflur, eða fyrst efnisflokk og svo töflu í honum."))
                        Text(t("Open “Shortlist” under an answer to see the tables it checked.", "Opnaðu „Stuttlisti“ undir svari til að sjá töflurnar sem voru skoðaðar."))
                            .font(.caption).foregroundStyle(pal.text3)
                    }
                }

                Card {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(t("Limits", "Takmarkanir")).font(.headline).foregroundStyle(pal.text)
                        ForEach([
                            t("Each part of a question is answered from one table.", "Hver hluti spurningar er svaraður úr einni töflu."),
                            t("Numbers are as new as the snapshot; the answer says when the data is older than asked.", "Tölurnar eru jafn nýjar og afritið; svarið segir ef gögnin eru eldri en spurt var um."),
                            t("It can pick the wrong table. The source chip links to the table so you can check.", "Það getur valið ranga töflu. Heimildarhnappurinn vísar á töfluna svo þú getir athugað."),
                        ], id: \.self) { s in
                            Label(s, systemImage: "minus").font(.callout).foregroundStyle(pal.text2)
                        }
                    }
                }

                Card {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(t("Voice", "Rödd")).font(.headline).foregroundStyle(pal.text)
                        Text(t("Speech recognition by Whisper large-v3-turbo (OpenAI, MIT licence). Spoken answers in English by Kokoro-82M (Apache-2.0), and in Icelandic by Piper (GPL-3.0) with the voice Steinn, trained on Talrómur from Reykjavík University and Almannarómur (CC BY 4.0). Everything runs on the team's own machine.",
                               "Talgreining með Whisper large-v3-turbo (OpenAI, MIT-leyfi). Talgerving á ensku með Kokoro-82M (Apache-2.0) og á íslensku með Piper (GPL-3.0) og röddinni Steini, þjálfaðri á Talrómi frá Háskólanum í Reykjavík og Almannarómi (CC BY 4.0). Allt keyrir á vél liðsins."))
                            .font(.footnote).foregroundStyle(pal.text2)
                    }
                }

                Text(t("Team MOMS · Gagnaþon 2026", "Lið MOMS · Gagnaþon 2026")).font(.caption).foregroundStyle(pal.text3)
                    .frame(maxWidth: .infinity)
            }
            .padding(16)
        }
        .background(pal.bg)
        .navigationTitle(t("About", "Um"))
    }

    private func section(_ title: String, steps: [(String, String, String)]) -> some View {
        Card {
            VStack(alignment: .leading, spacing: 14) {
                Text(title).font(.headline).foregroundStyle(pal.text)
                ForEach(Array(steps.enumerated()), id: \.offset) { _, s in
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: s.0).font(.body).foregroundStyle(pal.accent).frame(width: 28)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(s.1).font(.subheadline.weight(.semibold)).foregroundStyle(pal.text)
                            Text(s.2).font(.footnote).foregroundStyle(pal.text2).fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
    }

    private func tier(_ n: String, _ title: String, _ body: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text(n).font(.caption.bold()).foregroundStyle(pal.accentInk)
                .frame(width: 24, height: 24).background(pal.accent, in: Circle())
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(pal.text)
                Text(body).font(.footnote).foregroundStyle(pal.text2).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
