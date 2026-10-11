# Script examples

Vetted patterns for `/mf script`. Every example fits the 255-byte limit, passes the script checker
and runs without errors against a fake game in the test suite (`tests/test_script_examples.py`).
Whether a given game function exists on the live client is confirmed in game (see
`docs/TESTING.md`); a name that fails there is removed or replaced here. Adapt them, do not copy
blindly: change the names, numbers and messages to fit the request.

## Information

### Show your coordinates
```
/run local m=C_Map.GetBestMapForUnit("player");local p=C_Map.GetPlayerMapPosition(m,"player");print(format("%s %.1f, %.1f",GetZoneText(),p.x*100,p.y*100))
```
Prints the zone and your map position in chat.

### Free bag slots
```
/run local f=0;for b=0,4 do f=f+C_Container.GetContainerNumFreeSlots(b)end;print(f.." free bag slots")
```
Adds up the free slots of the backpack and four bags.

### Average armor durability
```
/run local s,n=0,0;for i=1,18 do local c,m=GetInventoryItemDurability(i);if c then s=s+c/m;n=n+1 end end;print(format("Durability %d%%",n>0 and s/n*100 or 100))
```
Averages the durability of everything you wear.

### Latency
```
/run local _,_,h,w=GetNetStats();print(h.."ms home, "..w.."ms world")
```

### Frame rate
```
/run print(format("%.0f fps",GetFramerate()))
```

### Experience to the next level
```
/run print(format("XP %d%%",UnitXP("player")/UnitXPMax("player")*100))
```

### Your gold
```
/run print(GetMoneyString(GetMoney()))
```

### Who is my target
```
/run if UnitExists("target")then print(UnitName("target"),UnitLevel("target"),UnitClass("target"))end
```

### Combat state
```
/run print(UnitAffectingCombat("player")and"In combat"or"Out of combat")
```

### The time
```
/run print(date("%H:%M"))
```

## Settings (one change per press)

### Toggle sound effects
```
/run SetCVar("Sound_EnableSFX",GetCVar("Sound_EnableSFX")=="1"and 0 or 1)
```

### Toggle music
```
/run SetCVar("Sound_EnableMusic",GetCVar("Sound_EnableMusic")=="1"and 0 or 1)
```

### Toggle all sound
```
/run SetCVar("Sound_EnableAllSound",GetCVar("Sound_EnableAllSound")=="1"and 0 or 1)
```

### Zoom the camera out as far as the game allows
```
/run SetCVar("cameraDistanceMaxZoomFactor",2.6)
```

## Fun

### Random pet name (never the same twice in a row)
```
/run local n={"Milo","Biscuit","Rascal","Pickles","Waffles","Mochi"};local i=random(#n);if i==MF_p then i=i%#n+1 end;MF_p=i;C_PetInfo.PetRename(n[i])
```
The global `MF_p` remembers the last pick between presses.

### Magic 8-ball
```
/run local a={"Yes","No","Maybe","Ask again later","Definitely not"};print(a[random(#a)])
```

### Random emote
```
/run local e={"dance","cheer","laugh","wave","bow"};DoEmote(e[random(#e)])
```

### Mark your target with a skull
```
/run SetRaidTarget("target",8)
```

### Cycle the raid mark on your target
```
/run MF_m=((MF_m or 0)%8)+1;SetRaidTarget("target",MF_m)
```
