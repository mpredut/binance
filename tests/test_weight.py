 #### test
 
import time
import datetime

#my imports
import priceAnalysis as pa


symbol = "BTCUSDC"

while (True):
    weight = pa.get_weight_for_cash_permission_at_quant_time(symbol)
    if weight is None:
        print(f"Weight is None, set it at default 0.03")
        weight = 0.03
    else:
        print(f"Weight {weight} is applied to available. result {weight}")
    time.sleep(5)
