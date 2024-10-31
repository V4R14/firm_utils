//SPDX-License-Identifier: MIT
/****
 ***** this code and any deployments of this code are strictly provided as-is;
 ***** no guarantee, representation or warranty is being made, express or implied, as to the safety or correctness of the code
 ***** or any smart contracts or other software deployed from these files,
 ***** in accordance with the disclosures and licenses found here: https://github.com/V4R14/firm_utils/blob/main/LICENSE
 ***** this code is not audited, and users, developers, or adapters of these files should proceed with caution and use at their own risk.
 ****/

pragma solidity >=0.8.18;

/// @notice interface for the immutable UniswapV2 router, `UNI_ROUTER_ADDR` in this contract
interface IUniswapV2Router02 {
    function swapExactETHForTokens(
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external payable returns (uint256[] memory amounts);
}

/**
 * @title      Pay ETH to USDC
 *
 * @author     Varia LLC
 *
 * @notice     Non-custodial auto-converter of wei to USDC, enabling service pricing denominated in ETH but ultimate receipt of USDC
 *
 * @dev        Uses Uniswap V2 router to swap incoming ETH for USDC tokens, then sends to `receiver` (initially, the deployer)
 */
contract PayETHToUSDC {
    address constant UNI_ROUTER_ADDR =
        0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D;
    address constant USDC_ADDR = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;
    address constant WETH_ADDR = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2;

    IUniswapV2Router02 immutable uniRouter;

    address public receiver;

    event PayETHToUSDC_PaymentReceived(
        uint256 weiAmount,
        address payor,
        address receiver
    );
    event PayETHToUSDC_ReceiverUpdated(address newReceiver);

    error PayETHToUSDC_CallerNotCurrentReceiver();

    constructor() payable {
        uniRouter = IUniswapV2Router02(UNI_ROUTER_ADDR);
        receiver = msg.sender;
    }

    /// @notice receives ETH payment and swaps to USDC via UniswapV2 router, which is then sent to receiver
    /// @custom:security recommended to implement a "minAmountOut" check in place of "1" due to sandwiching risks
    receive() external payable {
        uniRouter.swapExactETHForTokens{value: msg.value}(
            1,
            _getPathForETHtoUSDC(),
            receiver,
            block.timestamp
        );

        emit PayETHToUSDC_PaymentReceived(msg.value, msg.sender, receiver);
    }

    /// @notice allows current `receiver` to update its own address for payments
    /// @param _newReceiver new address to receive USDC tokens
    function changeReceiver(address _newReceiver) external {
        if (msg.sender != receiver)
            revert PayETHToUSDC_CallerNotCurrentReceiver();
        receiver = _newReceiver;

        emit PayETHToUSDC_ReceiverUpdated(_newReceiver);
    }

    /// @notice helper function to format `path`
    /// @return path the `uniRouter` path for ETH/USDC swap
    function _getPathForETHtoUSDC() internal pure returns (address[] memory) {
        address[] memory path = new address[](2);
        path[0] = WETH_ADDR;
        path[1] = USDC_ADDR;
        return path;
    }
}
