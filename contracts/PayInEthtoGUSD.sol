//SPDX-License-Identifier: MIT
/**** INCOMPLETE, DO NOT USE FOR ANY PURPOSE
***** this code and any deployments of this code are strictly provided as-is; no guarantee, representation or warranty is being made, express or implied, as to the safety or correctness of the code 
***** or any smart contracts or other software deployed from these files, in accordance with the disclosures and licenses found here: https://github.com/V4R14/firm_utils/blob/main/LICENSE
***** this code is not audited, and users, developers, or adapters of these files should proceed with caution and use at their own risk.
*****
***** TODO: check if an intermediate USDC step should be hardcoded using v2 swap contracts or if the intermediate step will be automatically accomplished
****/

pragma solidity >=0.8.0;

/// @title Pay In ETH to GUSD
/// @dev uses Uniswap router to swap incoming ETH for USDC then GUSD tokens, then sends to receiver address (initially, the deployer)
/// @notice permits payment for services denominated in ETH but receiving GUSD
interface ISwapRouter02 {
    function swapExactETHForTokens(uint256 amountOutMin, address[] calldata path, address to, uint256 deadline) external payable returns (uint256[] memory amounts);
}

contract PayInETHtoGUSD {

    address constant GUSD_TOKEN_ADDR = 0x056Fd409E1d7A124BD7017459dFEa2F387b6d5Cd; // GUSD mainnet token contract address
    //address constant USDC_TOKEN_ADDR = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48; // USDC mainnet token contract address
    address constant UNI_ROUTER_ADDR = 0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45; // Uniswap SwapRouter02 (1.1.0) router contract address
    address constant WETH_ADDR = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2; // WETH mainnet token address, alteratively could call uniRouter.WETH() for the path
    address receiver; 

    ISwapRouter02 public uniRouter;

    error CallerNotCurrentReceiver();

    constructor() payable {
        uniRouter = ISwapRouter02(UNI_ROUTER_ADDR);
        receiver = msg.sender;
    }

    /// @notice receives ETH payment and swaps to GUSD via Uniswap router, which is then sent to receiver
    receive() external payable {
        uniRouter.swapExactETHForTokens{ value: msg.value }(0, _getPathForETHtoGUSD(), receiver, block.timestamp);
    }

    /// @return the router path for ETH/GUSD swap
    function _getPathForETHtoGUSD() internal pure returns (address[] memory) {
        address[] memory path = new address[](2);
        path[0] = WETH_ADDR;
        path[1] = GUSD_TOKEN_ADDR;
        return path;
    }
    
    /// @notice allows current receiver address to change the receiver address for payments
    /// @param _newReceiver new address to receive ultimate stablecoin payment
    /// @return the receiver address
    function changeReceiver(address _newReceiver) external returns (address) {
        if (msg.sender != receiver) revert CallerNotCurrentReceiver();
        receiver = _newReceiver;
        return (receiver);
    }
}
