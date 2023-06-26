import json
import logging
import threading
import datetime

from kiteconnect import KiteConnect
from kitelogin import KiteLogin

logging.basicConfig(filename="main.log", level=logging.DEBUG)

# getting api and secret from login.json file
with open("login.json") as file:
    login = json.load(file)
    __api_key = login['api_key']
    __api_secret = login['api_secret']

kite = KiteConnect(api_key=__api_key)
# login using selenium in background
request_token = KiteLogin(login_url=kite.login_url()).get_request_token()
resp = kite.generate_session(request_token=request_token, api_secret=__api_secret)  # session created successfully
# kite.set_access_token(resp['access_token'])  # if generate_session can't set access_token then uncomment this line


# defining required functions
def get_new_request_token():
    return KiteLogin(login_url=kite.login_url()).get_request_token()


def get_ltp(exc="NSE", symbol="NIFTY 50") -> float:
    """
    returns only the ltp from KiteConnect.ltp

    :param exc: Exchange Code (i.e, NSE)
    :param symbol: tradingsymbol (i.e, INFY)
    :return: LTP
    """
    return kite.ltp(f"{exc}:{symbol}")[f"{exc}:{symbol}"]['last_price']


def get_ltp_from_inst_list(instruments: list[dict]) -> list[float]:
    """
    returns list of ltp in the same order of instruments

    :param instruments: list of instruments list[dict]
    :return: list of ltp
    """
    ltps = kite.ltp([f"{i['exchange']}:{i['tradingsymbol']}" for i in instruments])
    return [ltps[ltp]['last_price'] for ltp in ltps]


def get_atm(ltp, spd) -> float:
    """
    nearest strike to LTP

    ltp -> LTP of a particular option chain scrip/index (eg NIFTY 50, INFY, RELIANCE, ..)
    spd -> Strike Price Difference: difference between any two strikes of particular option chain
            example: nifty50 => spd -> 50 (17000, 17050, 17100, 17150, ...)

    :param ltp: ltp of nifty
    :param spd: strike price difference
    :return: At The Money strike
    """
    return round(ltp / spd) * spd


def refresh_data():
    """
    Refreshes instruments data

    :return: None
    """
    global ATM, TSUD, CE_OTM, PE_OTM
    ATM = get_atm(get_ltp(), SPD)  # at the money
    TSUD = [i for i in instruments if (ATM - SPD * 10) <= i["strike"] <= (ATM + SPD * 10)]  # ten strikes up down
    CE_OTM = [i for i in TSUD if i['instrument_type'] == "CE" and i["strike"] > ATM]
    PE_OTM = [i for i in TSUD if i['instrument_type'] == "PE" and i["strike"] < ATM]


# to get list of LTPs from instruments in PE_OTM and CE_OTM
def get_instrument(strike, instruments) -> dict:
    """
    returns the instrument from instruments where instrument['strike'] == strike

    :param strike: strike
    :param instruments: instruments list where strike is present
    :return: instrument dict
    """
    try:
        return [i for i in instruments if i['strike'] == strike][0]
    except Exception as exp:
        raise Exception(f"{exp}: No strike ({strike}) in instruments")


def place_order(tradingsymbol, ttype, quantity, sl):
    """
    places order

    :param tradingsymbol: trading symbol
    :param ttype: transaction type BUY/SELL
    :param quantity: quantity
    :param sl: stoploss
    :return: Order ID
    """

    try:
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            order_type=kite.ORDER_TYPE_SLM,  # Stop loss market
            tradingsymbol=tradingsymbol,
            transaction_type=ttype,
            quantity=quantity,
            product=kite.PRODUCT_MIS,  # intraday
            stoploss=sl
        )
    except Exception as exp:
        logging.warning(f"{ttype} order placement failed, error: {exp}")
        raise exp

    logging.info(f"{ttype} order placed order id: {order_id}")

    return order_id


def input_time() -> float:
    """
    Inputs time from user and returns the delay in seconds

    :return: float: delay in seconds
    """
    now = datetime.datetime.now()
    t = input(f"Enter time in 24H format (HH:MM) ({now.strftime('%H:%M:%S')}): ")
    now = now.now()
    user_date = datetime.datetime.strptime(f"{now.date()} {t if len(t) != 5 else t + ':00'}", "%Y-%m-%d %H:%M:%S")
    delay = (user_date - now).total_seconds()
    print(delay)
    if delay < 0:
        raise ValueError("Must enter future time")
    return delay


def sell_at(price, delay):
    """
    runs the sell function after some delay.

    delay is defined by user

    :param price: selling price
    :param delay: delay in seconds
    :return: thread id
    """
    tid = threading.Timer(delay, sell, args=(price,))
    logging.debug(f"Starting Thread, Thread Id: {tid}")
    tid.start()
    return tid


def sell(price):
    print()
    print("EXECUTING ORDER".center(100, "*"))
    print("Refreshing data...")
    refresh_data()

    print("fetching LTPs...")
    # zipped (strike_price, ltp) of every instrument in CE_OTM
    zipped_ce = sorted(tuple(zip([ce["strike"] for ce in CE_OTM], get_ltp_from_inst_list(CE_OTM))), key=lambda x: x[-1])

    # zipped (strike, ltp) of every instrument in PE_OTM
    zipped_pe = sorted(tuple(zip([pe["strike"] for pe in PE_OTM], get_ltp_from_inst_list(PE_OTM))), key=lambda x: x[-1])

    def fetch_strike_at_price(change: float = 0.05  # 5%
                              ) -> list[tuple, tuple]:
        """
        compares the LTPs from zipped_ce and zipped_pe to user entered price and returns the list of two list
        one contains CE strike, who's LTP is approximately equal to User entered ltp
        other contains PE strike, who's LTP is approximately equal to User entered ltp

        how much is approximate?
        default is 5% (change parameter). it means, if LTP is in range from 95% to 105% of user entered ltp
        then it will be considered as approximately equal.

        example: if user entered 200 as LTP then, 190 - 210 is considered as approx value (if `change` = 0.05)

        you can change approx by changing `change` parameter


        :param change: percentage, this percentage difference in ltp is considered as approx
        :return: [(ce_strike, ltp), (pe_strike, ltp)]
        """
        print("matching LTPs...")
        ce, pe = [], []
        for st, ltp in zipped_ce:
            if abs(price - ltp) <= ltp * change:
                ce.append((st, ltp))

        for st, ltp in zipped_pe:
            if abs(price - ltp) <= ltp * change:
                pe.append((st, ltp))

        if ce and pe:
            ce.sort(key=lambda x: x[0])
            pe.sort(key=lambda x: x[0], reverse=True)
            ce, pe = ce[0], pe[0]

        return [ce, pe]

    def fetch_strikes_with_similar_ltp(change: float = 0.05  # 5%
                                       ) -> list[list[tuple[float]]]:
        """
        this function works similar to the above one (fetch_strike_at_price), irrespective of price (user defined)
        this function compares zipped_ce against zipped_pe, to get similar LTPed CE and PE

        returns list of lists of tuples of floats

        :param change: percentage, percent difference considered to get approx value
        :return: 2D list of tuple contains STRIKE, LTP
        """
        pairs = []
        for st_ce, ce in zipped_ce:
            diff = ce * change
            for st_pe, pe in zipped_pe:
                if abs(pe - ce) < diff:
                    pairs.append([abs(st_pe - st_ce), (st_ce, ce), (st_pe, pe)])
        pairs.sort(key=lambda x: x[0])
        return [pair[1:] for pair in pairs]

    # price = 100
    ce, pe = fetch_strike_at_price(0.1)  # upto 10% difference is considered as approximate value

    if not (ce and pe):
        print(f"There is no option instrument trading near {price}")
        print("finding strikes with similar ltp..")
        similar_ltp_strikes = fetch_strikes_with_similar_ltp()
        print("found.")
        print()
        for i in range(len(similar_ltp_strikes)):
            data = similar_ltp_strikes[i]
            print(i, f"strike: {data[0][0]}CE -> ltp: {data[0][1]}, strike: {data[1][0]}PE -> ltp: {data[1][1]}",
                  '(near to ATM)' if i == 0 else "")

    else:
        ce_ins = get_instrument(ce[0], CE_OTM)
        pe_ins = get_instrument(pe[0], PE_OTM)

        qty = 1 * ce_ins['lot_size']  # 1 * 50

        print(f"strike: {ce[0]}CE, ltp: {ce[1]}")
        print(f"strike: {pe[0]}PE, ltp: {pe[1]}")

        print("placing sell order of", ce[0], "CE")
        ce_order_id = place_order(tradingsymbol=ce_ins['tradingsymbol'], quantity=qty, sl=ce[1] * 0.2,
                                  ttype=kite.TRANSACTION_TYPE_SELL)
        print(f"Order id: {ce_order_id}")
        print("placing sell order of", pe[0], "PE")
        pe_order_id = place_order(tradingsymbol=pe_ins['tradingsymbol'], quantity=qty, sl=pe[1] * 0.2,
                                  ttype=kite.TRANSACTION_TYPE_SELL)
        print(f"Order id: {pe_order_id}")


# all functions definitions over

now = datetime.datetime.now()
print(now)
print("Selected option chain -> NIFTY50")
print("getting instrument data")

# fetch all instruments for NIFTY
_instruments = sorted(
    [i for i in kite.instruments("NFO") if i["name"] == "NIFTY" and i['expiry'].month <= now.month + 2],
    key=lambda x: x['expiry'])

print("Fetching Expiry...")
# next expiry
expiry = sorted(list(set([i["expiry"] for i in _instruments])))[0]
print("Selected expiry ->", expiry)

# instruments at expiry
instruments = [i for i in _instruments if i['expiry'] == expiry]
strike_prices = sorted(list(set([i['strike'] for i in instruments])))

# Strike Price Difference
SPD = strike_prices[1] - strike_prices[0]

print("refreshing data...")
ATM = None
TSUD = CE_OTM = PE_OTM = []
refresh_data()

# taking user input to add order in queue 
if __name__ == "__main__":
    print("\n")
    while True:
        try:
            print("1.Add order to queue\n0.exit add order")
            if not int(input("Select option: ")):
                break
            price = int(input("Enter price: "))

            # get delay value
            while True:
                try:
                    print("when do you want to execute this order")
                    delay = input_time()
                    break
                except Exception as exp:
                    print(exp)
                    continue
            if sell_at(delay=delay, price=price):
                print("Order added to queue, successfullly")
        except Exception as ecp:
            print(ecp)
    print("DO NOT CLOSE THIS PROGRAM, otherwise you orders in queue will not be executed\n"
          "This program will automatically exit when there is no orders in queue")

