## PiSugar-python
PiSugar's independent Python driver, and there is no need to install PiSugar-server additionally. Just meet the requirements.

## Requirements

python 3.5+

## Usage

Python example

    from pisugar import *
    
    pisugar=PiSugarServer()
    pisugar.get_battery_level()  

NOTE: After the program initializes, it will launch a background process that retrieves information every ten seconds to facilitate the subsequent activation of the automatic low-battery shutdown feature.

## License

Apache License Version 2.0