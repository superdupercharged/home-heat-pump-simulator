- Küche: 12 qm, ein Fenster 1,4x1,4 m (Ug 0,6). Fußbodenheizung als Rücklauf. 
- Wohnzimmer: 45 qm, ein Fenster mit 1,9x2,1 m (Ug 0,6), eine Terrassentür mit 1,1x2,3 m (Ug 0,6) und eine Terrassentür mit 2,1x2,3 (Ug 0,6). 1 Heizkörper Typ 33 mit 2,2 x 0,3 m und 1 Heizkörper Typ 22 mit 0,9 x 0,6 m 
- Flur Erdgeschoss: 20 qm. Haustür mit 1,8 x 2,3 m (davon Fenster: 0,6x2 m), sonst keine Fenster. Heizkörper Typ 22 mit 1 x 1 m
- Büro: 15 qm, ein Fenster 1,9 x 1,4 m. Heizkörper Typ 22 mit 1,8 x 0,6 m. 
- Waschküche: 9 qm, ein Fenster 1,2 x 1,4 m. Heizkörper Typ 22 mit 0,8 x 0,6 m. 
- Heizungsraum: 5 qm ohne Fenster und mit einer Brandschutztür zur Garage. Kein Heizkörper. 
- Bad Erdgeschoss: 7 qm, ein Fenster 1,2 x 1,2 m. Heizkörper Typ 22 mit 0,6 x 0,6 m und zudem Fußbodenheizung als Rücklauf.
- Flur Obergeschoss: 22 qm, ein Fenster mit 1,4 x 1,4 m und eine Terrassentür mit 1,0 x 2,2 m. Heizkörper Typ 22 mit 1,4 x 0,6 m.
- Bad Obergeschoss: 6 qm, ein Dachfenster mit 1 x 0,5 m. 1 Sprossenheizkörper mit 1,6 x 0,5 m, zudem Fußbodenheizung als Rücklauf. 
- Zimmer 1: 13 qm, ein Fenster mit 1,4 x 1,4 m. Heizkörper Typ 22 mit 1,4 x 0,6 m.
- Zimmer 2: 13 qm, ein Fenster mit 1,4 x 1,4 m. Heizkörper Typ 22 mit 1,4 x 0,6 m.
- Zimmer 3: 15 qm, zwei Fenster mit jeweils 1,1 x 1,2 m. Heizkörper Typ 22 mit 1,8 x 0,6 m.
- Zimmer 4: 11 qm, ein Fenster mit 1,4 x 1,4 m. Heizkörper Typ 22 mit 1,2 x 0,6 m.
- Brutto-Grundfläche Außenmaß: 12,6 x 11 m mit einer 45° Schräge und einem Drempel von 90 cm Höhe im Obergeschoss. Kein Keller. Der Dachboden ist nicht beheizt. Es besteht eine Dämmung der letzten Geschossdecke mit 14 cm Glaswolle. 
- Energieeffizienz laut Energieausweis (Ausstellung VOR Austausch der 4 genannten Fenster/Terrassentüren im EG, die nun allesamt einen Ug-Wert von 0,6 haben): 119 kWh/m²/a, also jetzt wohl etwas besser. 
- Monatlicher Wasserverbrauch: 11 m³.
- Hausbau im Jahr 1993. Außenwand mit 24 cm Poroton, 6 cm Kerndämmung, 11,5 cm Verblender. 
        - Heizung derzeit mit Gasbrennwertheizung be von Buderus, Einbau 2018.



I have the following Szenario where I want to prevent a robot arm from crashing into an obstacle. The robot arm is manually operated so the path can not be planned collision free but rather the system has to react to the humans actions. The tcp to obstacle distance calculation will be done by a different ecu than the base function is running on. Therefore I need a very optimized interface. I want the controller running in the base ecu to act as soon as the distance gets below a warning threshold. Let’s say 50 Euclidean distance before collision. A stop-move stores hold shall play out 20cm before an actual collision. Therefore the tcp movement vector has to deceleration to 0 over the course of 30cm in the example. If the interface is designed minimally it only has room for a book value to mark the warning threshold and than the stop threshold. In that scenario it’s not possible to control the deceleration it would rather have to be tuned in a fashion that it reaches stop after 30 seconds. An issue arises when the velocity vector changes after the warning threshold happens and therefore a constant tuned deceleration happens. That would be very suboptimal. This lead to the conclusion that the bool warning once at threshold is not sufficient. A cyclical float to inform of the actual distance to collision would be perfect to control the deceleration. Downside is that the float takes a lot of bandwidth on the interface. I’m therefore looking for a middle ground solution. My current idea is that the distance calculating ecu could send a message of six ints corresponding to the six Cartesian directions (three axis +/- each) looking from the tool center point. In that message maybe each direction would be between 0 and 8 regarding severity of collision. The base Ecu could than interpolate that into the actual movement vector and than therefore accelerate ar decelerate or keep velocity constant. Would that approach even be more interface efficient? Do you other implementation ideas? Also educate me on typical standard robotics terms for this kind of problem