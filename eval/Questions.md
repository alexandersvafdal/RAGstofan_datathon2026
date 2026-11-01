# Questions

Test questions based only on the table names and variables in `data/tables_20261030_224153.json`. None have been run against the data.

† The question relies on a category name the catalog doesn't show, so check that it exists in the table.

## Easy: one table, direct lookup

| Question | Tables |
| --- | --- |
| What was the total population of Iceland in 2026? | `Ibuar/mannfjoldi/1_yfirlit/yfirlit_mannfjolda/MAN00000.px` |
| What was the mean age of the Icelandic population in 2000? | `Ibuar/mannfjoldi/1_yfirlit/yfirlit_mannfjolda/MAN00102.px` |
| What was the life expectancy at birth of females in 2025? | `Ibuar/Faeddirdanir/danir/danir/MAN05401.px` |
| How many boys were born alive in 2024? | `Ibuar/Faeddirdanir/faeddir/faedingar/MAN05100.px` |
| What was the total fertility rate in 2025? † | `Ibuar/Faeddirdanir/faeddir/faedingar/MAN05202.px` |
| How many foreign citizens lived in Iceland in 2020? | `Ibuar/mannfjoldi/3_bakgrunnur/Rikisfang/MAN04001.px` |
| How many passengers from abroad arrived on luxury liners at the Port of Reykjavík in 2019? | `Atvinnuvegir/ferdathjonusta/farthegar/1_farthegar/SAM02002.px` |
| How many rental cars were registered in July 2025? | `Atvinnuvegir/ferdathjonusta/ferdaidnadurhagvisar/SAM08049.px` |
| How many hours of sunshine did Reykjavík get in June 2020? | `Umhverfi/1_natturufar/2_vedurfar/UMH11165.px` |
| What was GDP per capita in 2020? | `Efnahagur/thjodhagsreikningar/landsframl/1_landsframleidsla/THJ01401.px` |
| What was the unemployment rate in Iceland in 2025? | `Samfelag/vinnumarkadur/vinnumarkadsrannsokn/3_arstolur/VIN00911.px` |
| What was the unadjusted gender pay gap in 2024? | `Samfelag/launogtekjur/1_laun/1_laun/VIN02010.px` |
| What was the consumer price index in January 2025? | `Efnahagur/visitolur/1_vnv/1_vnv/VIS01000.px` |
| What was the balance of trade in December 2025? | `Efnahagur/utanrikisverslun/1_voruvidskipti/01_voruskipti/UTA06002.px` |
| How much was collected in fishing fees in 2024? | `Umhverfi/6_graenahagkerfid/1_umhverfisskattar/UMH51102.px` |
| What were Iceland's total greenhouse gas emissions in CO2 equivalents in 2024? | `Umhverfi/2_losunlofttegunda/1_losunlofttegunda_nir/UMH31107.px` |
| How many motor vehicles were registered in 2024? | `Umhverfi/5_samgongur/3_okutaekiogvegir/1_okutaeki/SAM03105.px` |
| How many road traffic accidents were recorded in 2023? | `Umhverfi/5_samgongur/3_okutaekiogvegir/1_okutaeki/SAM03201.px` |
| What was the total expenditure on health care in 2025? | `Efnahagur/fjaropinber/fjarmal_heilbr/THJ05531.px` |
| What was R&D expenditure as a percentage of GDP in 2024? | `Atvinnuvegir/visinditaekni/rannsoknthroun/FYR05101.px` |
| How many women were elected to the Althingi in 2024? | `Ibuar/kosningar/althingi/althkjornir/KOS02055.px` |
| How many votes were cast in the 2024 presidential election? | `Ibuar/kosningar/forsetakosningar/KOS07100.px` |
| How many students passed the matriculation examination in 2025? | `Samfelag/skolamal/3_framhaldsskolastig/1_fsProf/SKO03200.px` |
| How many pupils per teacher were there in 2024? | `Samfelag/skolamal/2_grunnskolastig/0_gsNemendur/SKO02105.px` |
| What was the yearly operational cost per student in primary schools in 2025? | `Efnahagur/fjaropinber/fjarmal_fraedsla/1_utgjold_fraedsla/THJ05636A.px` |
| What was the Gini coefficient in 2022? | `Samfelag/launogtekjur/3_tekjur/2_tekjur_silc/LIF01110.px` |
| What was the at-risk-of-poverty threshold in 2023? | `Samfelag/lifskjor/3_fjarhagsstada/3_annad/LIF01151.px` |
| How many hospital beds were there in 2020? | `Samfelag/heilbrigdismal/heilbrigdisthjonusta/HEI04103.px` |
| How many museum visits were recorded in 2023? | `Samfelag/menning/2_sofn/1_sofn/MEN07002.px` |
| How many prisoners were there in 2013? | `Samfelag/domsmal/afbrot/DOM0502.px` |

## Medium: one table plus a computation

| Question | Tables |
| --- | --- |
| How many men were there per 100 women in Reykjavík in 2026? | `Ibuar/mannfjoldi/2_byggdir/sveitarfelog/MAN02005.px` |
| What share of Reykjavík's population held foreign citizenship in 2026? | `Ibuar/mannfjoldi/3_bakgrunnur/Rikisfang/MAN04203.px` |
| What percentage of the population was 65 or older in 2026? | `Ibuar/mannfjoldi/1_yfirlit/yfirlit_mannfjolda/MAN00101.px` |
| How many years higher was female life expectancy at birth than male in 2025? | `Ibuar/Faeddirdanir/danir/danir/MAN05401.px` |
| What share of live births in 2025 were to mothers under 20? | `Ibuar/Faeddirdanir/faeddir/faedingar/MAN05101.px` |
| What was the cumulative net external migration of foreign citizens from 2021 to 2025? | `Ibuar/buferlaflutningar/buferlaflmillilanda/MAN01400.px` |
| What share of all deaths in 2024 was caused by cancer? † | `Ibuar/Faeddirdanir/danir/danarmein/MAN05301.px` |
| By what percentage did passengers through Keflavik airport change from July 2024 to July 2025? | `Atvinnuvegir/ferdathjonusta/ferdaidnadurhagvisar/SAM02001.px` |
| What share of overnight stays in all registered accommodation in 2025 was by foreigners? | `Atvinnuvegir/ferdathjonusta/gisting/3_allartegundirgististada/SAM01601.px` |
| How many aircraft movements did Keflavik airport have in total in 2025? | `Atvinnuvegir/ferdathjonusta/ferdaidnadurhagvisar/SAM08048.px` |
| What was the average number of spectators per theatre performance in the 2019-2020 season? | `Samfelag/menning/1_listir/1_leiklist/MEN05107.px` |
| How many percentage points higher was hotel room occupancy in the capital region in July 2025 than in January 2025? † | `Atvinnuvegir/ferdathjonusta/gisting/1_hotelgistiheimili/SAM01104.px` |
| What was real GDP growth from 2024 to 2025? | `Efnahagur/thjodhagsreikningar/landsframl/1_landsframleidsla/THJ01103.px` |
| What was the balance of goods trade for the whole of 2025, adding up the months? | `Efnahagur/utanrikisverslun/1_voruvidskipti/01_voruskipti/UTA06002.px` |
| What share of Iceland's goods exports went to the Netherlands in 2025? † | `Efnahagur/utanrikisverslun/1_voruvidskipti/01_voruskipti/UTA06003.px` |
| What share of service exports came from travel in 2025? † | `Efnahagur/utanrikisverslun/2_thjonusta/thjonusta/UTA04006.px` |
| What was the percentage change in the annual average CPI from 2024 to 2025? | `Efnahagur/visitolur/1_vnv/1_vnv/VIS01005.px` |
| By what percentage did the annual average building cost index rise from 2021 to 2025? | `Efnahagur/visitolur/2_byggingarvisitala/byggingarvisitala/VIS13401.px` |
| By what percentage did the annual wage index rise from 2015 to 2025? | `Samfelag/launogtekjur/2_lvt/3_arstolur/LAU04200.px` |
| What were women's earnings as a percentage of men's among full-time managers in 2025? † | `Samfelag/launogtekjur/1_laun/1_laun/VIN02001.px` |
| How many percentage points did the unemployment rate change between 2019 and 2021? | `Samfelag/vinnumarkadur/vinnumarkadsrannsokn/3_arstolur/VIN00911.px` |
| What percentage of students at the tertiary level in autumn 2025 were female? | `Samfelag/skolamal/0_yfirlit/yfirlit/SKO00000.px` |
| What share of general government total expenditure went to health in 2025? † | `Efnahagur/fjaropinber/fjarmal_opinber/fjarmal_opinber/THJ05142.px` |
| By what percentage did greenhouse gas emissions in CO2 equivalents change from 1990 to 2024? | `Umhverfi/2_losunlofttegunda/1_losunlofttegunda_nir/UMH31107.px` |
| What share of energy consumed by economic sectors in 2024 was renewable? | `Umhverfi/4_orkumal/3_orkuflaedireikningar/UMH40110.px` |
| What share of vehicles in 2025 ran on electricity? † | `Umhverfi/5_samgongur/3_okutaekiogvegir/1_okutaeki/SAM30120.px` |
| What percentage of candidates in the 2024 general election were under 30? | `Ibuar/kosningar/althingi/althframbjodendur/KOS02108.px` |
| How many more hours of sunshine did Reykjavík get than Akureyri over the whole of 2020? | `Umhverfi/1_natturufar/2_vedurfar/UMH11165.px` |
| What share of the catch of Icelandic vessels in 2024 was cod? † | `Atvinnuvegir/sjavarutvegur/aflatolur/fiskveidisvaedi/SJA09005.px` |
| What was the average value per tonne of cod caught in 2024? † | `Atvinnuvegir/sjavarutvegur/aflatolur/fiskveidisvaedi/SJA09001.px` |

## High: two or more tables

| Question | Tables |
| --- | --- |
| How many overnight stays did foreigners register in 2025 for each foreign passenger through Keflavik airport? | `Atvinnuvegir/ferdathjonusta/gisting/3_allartegundirgististada/SAM01601.px`<br>`Atvinnuvegir/ferdathjonusta/ferdaidnadurhagvisar/SAM02001.px` |
| What was the average number of passengers per aircraft movement at Keflavik airport in 2024? | `Atvinnuvegir/ferdathjonusta/ferdaidnadurhagvisar/SAM02001.px`<br>`Atvinnuvegir/ferdathjonusta/ferdaidnadurhagvisar/SAM08048.px` |
| What was inbound tourism expenditure per foreign passenger through Keflavik airport in 2024? | `Efnahagur/thjodhagsreikningar/ferdathjonustureikningar/ferdathjonustureikningar/SAM08000.px`<br>`Atvinnuvegir/ferdathjonusta/ferdaidnadurhagvisar/SAM02001.px` |
| How many hotel and guesthouse overnight stays were there per hotel bedroom in the capital region in 2025? † | `Atvinnuvegir/ferdathjonusta/gisting/1_hotelgistiheimili/SAM01201.px`<br>`Atvinnuvegir/ferdathjonusta/gisting/1_hotelgistiheimili/SAM01202.px` |
| What were greenhouse gas emissions per inhabitant in 2024? | `Umhverfi/2_losunlofttegunda/1_losunlofttegunda_nir/UMH31107.px`<br>`Ibuar/mannfjoldi/1_yfirlit/yfirlit_mannfjolda/MAN00101.px` |
| How did greenhouse gas emissions per unit of real GDP change between 2014 and 2024? | `Umhverfi/2_losunlofttegunda/1_losunlofttegunda_nir/UMH31107.px`<br>`Efnahagur/thjodhagsreikningar/landsframl/1_landsframleidsla/THJ01103.px` |
| What were road transport greenhouse gas emissions per registered motor vehicle in 2024? | `Umhverfi/2_losunlofttegunda/2_losunlofttegunda_aea/UMH31119.px`<br>`Umhverfi/5_samgongur/3_okutaekiogvegir/1_okutaeki/SAM03105.px` |
| How many road traffic accidents were there per 1,000 registered motor vehicles in 2023? | `Umhverfi/5_samgongur/3_okutaekiogvegir/1_okutaeki/SAM03201.px`<br>`Umhverfi/5_samgongur/3_okutaekiogvegir/1_okutaeki/SAM03105.px` |
| How much electricity was used per inhabitant in 2024? | `Umhverfi/4_orkumal/3_orkuflaedireikningar/UMH40210.px`<br>`Ibuar/mannfjoldi/1_yfirlit/yfirlit_mannfjolda/MAN00101.px` |
| How many kilos of waste were collected per inhabitant in 2022? | `Umhverfi/3_efnisflaedi/2_urgangur/UMH04105.px`<br>`Ibuar/mannfjoldi/1_yfirlit/yfirlit_mannfjolda/MAN08000.px` |
| What share of electricity generated by public power plants in 2019 went to heavy industry? | `Umhverfi/4_orkumal/2_framleidslaognotkun/IDN02103.px`<br>`Umhverfi/4_orkumal/2_framleidslaognotkun/IDN02101.px` |
| What was the fishing fee as a percentage of net profit in fishing in 2024? | `Umhverfi/6_graenahagkerfid/1_umhverfisskattar/UMH51102.px`<br>`Atvinnuvegir/sjavarutvegur/afkomasja/SJA08107.px` |
| What share of central government total revenue in 2024 came from environmental and resource-related taxes? | `Umhverfi/6_graenahagkerfid/1_umhverfisskattar/UMH51101.px`<br>`Efnahagur/fjaropinber/fjarmal_rikissjods/THJ05232.px` |
| What share of Iceland's total goods exports in 2025 were marine products? | `Atvinnuvegir/sjavarutvegur/utf/SJA04903.px`<br>`Efnahagur/utanrikisverslun/1_voruvidskipti/01_voruskipti/UTA06002.px` |
| What percentage of farmed salmon production in 2025, by tonnes, was exported? † | `Atvinnuvegir/sjavarutvegur/fiskeldi/SJA07003.px`<br>`Atvinnuvegir/sjavarutvegur/fiskeldi/SJA07001.px` |
| How much lamb meat was produced per sheep in 2025? † | `Atvinnuvegir/landbunadur/landframleidsla/LAN10201.px`<br>`Atvinnuvegir/landbunadur/landbufe/LAN10102.px` |
| What was net profit in fishing per person employed in fishing in 2024? † | `Atvinnuvegir/sjavarutvegur/afkomasja/SJA08107.px`<br>`Samfelag/vinnumarkadur/vinnumarkadsrannsokn/3_arstolur/VIN01105.px` |
| Did building costs or consumer prices rise faster between 2021 and 2025, on annual averages? | `Efnahagur/visitolur/2_byggingarvisitala/byggingarvisitala/VIS13401.px`<br>`Efnahagur/visitolur/1_vnv/1_vnv/VIS01005.px` |
| Did residential property prices or wages rise faster between January 2015 and January 2025? | `Efnahagur/visitolur/1_vnv/3_greiningarvisitolur/VIS01106.px`<br>`Samfelag/launogtekjur/2_lvt/1_manadartolur/LAU04000.px` |
| From January 2015 to January 2025, rank residential property prices, wages and consumer prices by growth. | `Efnahagur/visitolur/1_vnv/3_greiningarvisitolur/VIS01106.px`<br>`Samfelag/launogtekjur/2_lvt/1_manadartolur/LAU04000.px`<br>`Efnahagur/visitolur/1_vnv/1_vnv/VIS01000.px` |
| How many unemployed people were there per vacant position in the first quarter of 2026? | `Samfelag/vinnumarkadur/vinnumarkadsrannsokn/2_arsfjordungstolur/VIN00920.px`<br>`Samfelag/vinnumarkadur/lausstorf/JVS00001.px` |
| Using the cost per student, what was the implied total operating cost of compulsory schools in 2024? | `Efnahagur/fjaropinber/fjarmal_fraedsla/1_utgjold_fraedsla/THJ05636A.px`<br>`Samfelag/skolamal/0_yfirlit/yfirlit/SKO00000.px` |
| How many children were there per staff member in pre-primary institutions in 2024? | `Samfelag/skolamal/1_leikskolastig/0_lsNemendur/SKO01101.px`<br>`Samfelag/skolamal/1_leikskolastig/1_lsStarfsfolk/SKO01300.px` |
| How many live births per 1,000 inhabitants did Reykjavík have in 2025? | `Ibuar/Faeddirdanir/faeddir/faedingar/MAN05110.px`<br>`Ibuar/mannfjoldi/2_byggdir/sveitarfelog/MAN02005.px` |
| How many deaths per 1,000 inhabitants did Akureyri have in 2024? | `Ibuar/Faeddirdanir/danir/danir/MAN05221.px`<br>`Ibuar/mannfjoldi/2_byggdir/sveitarfelog/MAN02008.px` |
| What share of Reykjavík's inhabitants were immigrants in 2026? | `Ibuar/mannfjoldi/3_bakgrunnur/Uppruni/MAN43005.px`<br>`Ibuar/mannfjoldi/2_byggdir/sveitarfelog/MAN02005.px` |
| Was turnout higher in the 2024 presidential election or the 2024 general election? | `Ibuar/kosningar/forsetakosningar/KOS07000.px`<br>`Ibuar/kosningar/althingi/althkjosendur/KOS02101a.px` |
| What share of the population was on the electoral roll for the 2024 general election? | `Ibuar/kosningar/althingi/althkjosendur/KOS02100.px`<br>`Ibuar/mannfjoldi/1_yfirlit/yfirlit_mannfjolda/MAN00101.px` |
| How many divorces were there per marriage in 2020? | `Ibuar/fjolsk/giftingarogskilnadir/giftingarogskilnadir/MAN06201.px`<br>`Ibuar/fjolsk/giftingarogskilnadir/giftingarogskilnadir/MAN06100.px` |
| How many museum visits were there per inhabitant in 2023? | `Samfelag/menning/2_sofn/1_sofn/MEN07002.px`<br>`Ibuar/mannfjoldi/1_yfirlit/yfirlit_mannfjolda/MAN00101.px` |
