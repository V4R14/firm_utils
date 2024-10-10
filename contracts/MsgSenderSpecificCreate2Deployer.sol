//SPDX-License-Identifier: MIT

pragma solidity 0.8.18;

/*
 *
 * @notice CREATE2 contract deployer for predictable addresses based on the `msg.sender` and their passed `salt`
 *
 * @dev replace `Contract` with the contract to be CREATE2-deployed, and adjust functions for any constructor params
 *
 **/

contract Contract {
    constructor() payable {}
}

/// @title Msg.Sender-Specific Create2 Deployer
contract MsgSenderSpecificCreate2Deployer {
    event Deployment(address newContract);

    /// @notice deploys a new `Contract` via CREATE2
    /// @param salt uint256 which will be packed with `msg.sender` for the CREATE2 `salt` value
    function deploy(uint256 salt) external returns (address) {
        bytes32 _salt = keccak256(abi.encodePacked(msg.sender, salt));
        Contract _contract = new Contract{salt: _salt, value: 0}();
        address _newContract = address(_contract);

        emit Deployment(_newContract);
        return _newContract;
    }

    /// @notice returns the predicted new contract address for `Contract` if `msg.sender` calls `deploy()` passing `salt`
    /// @param salt uint256 which will be packed with `msg.sender` for the CREATE2 `salt` value
    function getAddress(uint256 salt) public view returns (address) {
        bytes32 _salt = keccak256(abi.encodePacked(msg.sender, salt));
        bytes32 hash = keccak256(abi.encodePacked(bytes1(0xff), address(this), _salt, keccak256(getBytecode())));
        return address(uint160(uint256(hash)));
    }

    /// @notice returns the creation bytecode for `Contract`
    function getBytecode() public pure returns (bytes memory) {
        bytes memory bytecode = type(Contract).creationCode;
        return abi.encodePacked(bytecode);
    }
}
